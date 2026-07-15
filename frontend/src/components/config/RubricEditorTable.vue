<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ConfigEditorEdit, ConfigEditorIssue, ConfigEditorRow } from '../../api/config-workspace'

const props = withDefaults(defineProps<{
  rows: ConfigEditorRow[]
  totalScore: number
  issues?: ConfigEditorIssue[]
  disabled?: boolean
}>(), {
  issues: () => [],
  disabled: false,
})

const emit = defineEmits<{
  edit: [edit: ConfigEditorEdit]
  validity: [valid: boolean]
}>()
const root = ref<HTMLElement | null>(null)
const scoreErrors = ref<Record<string, string>>({})
const blockingIssues = computed(() => props.issues.filter((issue) => issue.severity === 'error'))
const warningIssues = computed(() => props.issues.filter((issue) => issue.severity !== 'error'))
const totalBlocked = computed(() => props.totalScore !== 100)
const firstRowIds = computed(() => {
  const seen = new Set<string>()
  const first = new Set<string>()
  for (const row of props.rows) {
    if (seen.has(row.question_id)) continue
    seen.add(row.question_id)
    first.add(row.row_id)
  }
  return first
})

function identity(row: ConfigEditorRow): string {
  return `${row.question_id} ${row.part_id} ${row.step_id}`
}

function validateScore(row: ConfigEditorRow, event: Event): number | null {
  const raw = (event.currentTarget as HTMLInputElement).value.trim()
  const value = Number(raw)
  if (!raw || !Number.isFinite(value) || value < 0 || value > 100) {
    scoreErrors.value[row.row_id] = '分值必须是 0 至 100 之间的有效数字。'
    emit('validity', false)
    return null
  }
  delete scoreErrors.value[row.row_id]
  emit('validity', Object.keys(scoreErrors.value).length === 0)
  return value
}

function numberEdit(row: ConfigEditorRow, event: Event): void {
  const value = validateScore(row, event)
  if (value === null) return
  emit('edit', { row_id: row.row_id, score: value })
}

function textEdit(row: ConfigEditorRow, field: 'standard_answer', event: Event): void {
  emit('edit', { row_id: row.row_id, [field]: (event.currentTarget as HTMLTextAreaElement).value })
}

function acceptedEdit(row: ConfigEditorRow, event: Event): void {
  const accepted_answers = (event.currentTarget as HTMLTextAreaElement).value
    .split(/\r?\n/).map((answer) => answer.trim()).filter(Boolean)
  emit('edit', { row_id: row.row_id, accepted_answers })
}

function listEdit(
  row: ConfigEditorRow,
  field: 'required_elements' | 'deduction_rules',
  event: Event,
): void {
  const values = (event.currentTarget as HTMLTextAreaElement).value
    .split(/\r?\n/).map((value) => value.trim()).filter(Boolean)
  emit('edit', { row_id: row.row_id, [field]: values })
}

function policyNumberEdit(row: ConfigEditorRow, event: Event): void {
  const raw = (event.currentTarget as HTMLInputElement).value.trim()
  if (!raw) {
    emit('edit', { row_id: row.row_id, answer_only_max_score: null })
    return
  }
  const value = Number(raw)
  if (!Number.isFinite(value) || value < 0 || value > 100) return
  emit('edit', { row_id: row.row_id, answer_only_max_score: value })
}

function policyRequiredEdit(row: ConfigEditorRow, event: Event): void {
  emit('edit', { row_id: row.row_id,
    require_final_answer: (event.currentTarget as HTMLInputElement).checked })
}

function policyRuleEdit(row: ConfigEditorRow, event: Event): void {
  emit('edit', { row_id: row.row_id,
    final_answer_rule: (event.currentTarget as HTMLTextAreaElement).value })
}

type RenderedEditField = 'score' | 'standard_answer' | 'accepted_answers'
  | 'required_elements' | 'deduction_rules' | 'answer_only_max_score'
  | 'require_final_answer' | 'final_answer_rule'

function fieldForIssue(issue: ConfigEditorIssue): RenderedEditField | null {
  if (issue.field === 'score') return 'score'
  if (issue.field === 'accepted_answers') return 'accepted_answers'
  if (issue.field === 'standard_answer') return 'standard_answer'
  if (issue.field === 'required_elements') return 'required_elements'
  if (issue.field === 'deduction_rules') return 'deduction_rules'
  if (issue.field === 'answer_only_max_score') return 'answer_only_max_score'
  if (issue.field === 'require_final_answer') return 'require_final_answer'
  if (issue.field === 'final_answer_rule') return 'final_answer_rule'
  return null
}

function focusIssue(issue: ConfigEditorIssue): void {
  if (issue.row_id === null) return
  const field = fieldForIssue(issue)
  if (field === null) return
  const row = [...(root.value?.querySelectorAll<HTMLElement>('[data-row-id]') ?? [])]
    .find((candidate) => candidate.dataset.rowId === issue.row_id)
  const target = [...(row?.querySelectorAll<HTMLElement>('[data-edit-field]') ?? [])]
    .find((candidate) => candidate.dataset.editField === field)
  target?.focus()
  target?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
}
</script>

<template>
  <section ref="root" class="rubric-ledger" aria-labelledby="rubric-ledger-title">
    <header class="config-section-heading rubric-ledger__heading">
      <div>
        <h2 id="rubric-ledger-title" tabindex="-1">编辑评分依据</h2>
        <p>题号、评分单元和匹配规则由服务器维护，此处只编辑允许修改的内容。</p>
      </div>
      <strong
        class="rubric-ledger__total"
        :class="{ 'rubric-ledger__total--blocked': totalBlocked }"
        :data-save-blocked="totalBlocked ? 'true' : 'false'"
      >总分 {{ totalScore }} / 100</strong>
    </header>

    <div v-if="totalBlocked || blockingIssues.length" class="rubric-ledger__issues rubric-ledger__issues--blocking" role="alert">
      <strong>当前不能保存</strong>
      <p v-if="totalBlocked">总分需调整为 100 分。</p>
      <button
        v-for="issue in blockingIssues"
        :key="`${issue.code}:${issue.row_id}:${issue.field}`"
        type="button"
        :data-issue-row-id="issue.row_id ?? undefined"
        :data-issue-field="issue.field"
        @click="focusIssue(issue)"
      >{{ issue.message }}</button>
    </div>
    <div v-if="warningIssues.length" class="rubric-ledger__issues rubric-ledger__issues--warning" role="status">
      <strong>请核对</strong>
      <button
        v-for="issue in warningIssues"
        :key="`${issue.code}:${issue.row_id}:${issue.field}`"
        type="button"
        :data-issue-row-id="issue.row_id ?? undefined"
        :data-issue-field="issue.field"
        @click="focusIssue(issue)"
      >{{ issue.message }}</button>
    </div>

    <div class="rubric-ledger__viewport" tabindex="0" aria-label="评分依据编辑表">
      <table>
        <thead>
          <tr>
            <th class="rubric-ledger__sticky rubric-ledger__sticky--question" scope="col">题号</th>
            <th class="rubric-ledger__sticky rubric-ledger__sticky--part" scope="col">评分单元</th>
            <th scope="col">评分点</th>
            <th scope="col">匹配规则</th>
            <th scope="col">分值</th>
            <th scope="col">等价答案</th>
            <th scope="col">标准答案</th>
            <th scope="col">证据要求/关键步骤</th>
            <th scope="col">扣分规则</th>
            <th scope="col">整题策略</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.row_id" :data-row-id="row.row_id">
            <th class="rubric-ledger__sticky rubric-ledger__sticky--question" scope="row">{{ row.question_id }}</th>
            <td class="rubric-ledger__sticky rubric-ledger__sticky--part">
              <strong>{{ row.part_label }}</strong>
              <small>{{ row.part_id }} / {{ row.step_id }}</small>
            </td>
            <td class="rubric-ledger__goal">{{ row.core_goal || '未提供评分点' }}</td>
            <td class="rubric-ledger__match">{{ row.match_rule || '按评分依据判定' }}</td>
            <td>
              <input
                type="number"
                min="0"
                max="100"
                step="0.5"
                data-edit-field="score"
                :aria-label="`${identity(row)} 分值`"
                :value="row.score"
                :aria-invalid="scoreErrors[row.row_id] ? 'true' : 'false'"
                :disabled="disabled"
                @input="validateScore(row, $event)"
                @change="numberEdit(row, $event)"
              >
              <small v-if="scoreErrors[row.row_id]" class="rubric-ledger__field-error" role="alert">
                {{ scoreErrors[row.row_id] }}
              </small>
            </td>
            <td>
              <textarea
                rows="4"
                data-edit-field="accepted_answers"
                :aria-label="`${identity(row)} 等价答案`"
                :value="row.accepted_answers.join('\n')"
                :disabled="disabled"
                @change="acceptedEdit(row, $event)"
              />
            </td>
            <td>
              <textarea
                rows="4"
                data-edit-field="standard_answer"
                :aria-label="`${identity(row)} 标准答案`"
                :value="row.standard_answer"
                :disabled="disabled"
                @change="textEdit(row, 'standard_answer', $event)"
              />
            </td>
            <td>
              <textarea
                rows="4"
                data-edit-field="required_elements"
                :aria-label="`${identity(row)} 证据要求/关键步骤`"
                :value="row.required_elements.join('\n')"
                :disabled="disabled"
                @change="listEdit(row, 'required_elements', $event)"
              />
            </td>
            <td>
              <textarea
                rows="4"
                data-edit-field="deduction_rules"
                :aria-label="`${identity(row)} 扣分规则`"
                :value="row.deduction_rules.join('\n')"
                :disabled="disabled"
                @change="listEdit(row, 'deduction_rules', $event)"
              />
            </td>
            <td>
              <div v-if="firstRowIds.has(row.row_id)" class="rubric-ledger__policy">
                <label>
                  <input
                    type="checkbox"
                    data-edit-field="require_final_answer"
                    :aria-label="`${row.question_id} 要求最终答案`"
                    :checked="row.require_final_answer === true"
                    :disabled="disabled"
                    @change="policyRequiredEdit(row, $event)"
                  >
                  要求最终答案
                </label>
                <label>
                  <span>仅答案最高分</span>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    step="0.5"
                    data-edit-field="answer_only_max_score"
                    :aria-label="`${row.question_id} 仅答案最高分`"
                    :value="row.answer_only_max_score ?? ''"
                    :disabled="disabled"
                    @change="policyNumberEdit(row, $event)"
                  >
                </label>
                <textarea
                  rows="3"
                  data-edit-field="final_answer_rule"
                  :aria-label="`${row.question_id} 最终答案规则`"
                  :value="row.final_answer_rule"
                  :disabled="disabled"
                  @change="policyRuleEdit(row, $event)"
                />
              </div>
              <small v-else>整题策略见首行</small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
