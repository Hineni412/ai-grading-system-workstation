<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

import type { ConfigEditorEdit, ConfigEditorIssue, ConfigEditorRow } from '../../api/config-workspace'

const props = withDefaults(defineProps<{
  rows: ConfigEditorRow[]
  totalScore: number
  issues?: ConfigEditorIssue[]
  disabled?: boolean
  showRegenerationActions?: boolean
  canRegenerateBatched?: boolean
  canRegenerateWholeDocument?: boolean
  regenerationBusy?: boolean
  regenerationMode?: 'batched' | 'whole_document' | null
  regenerationSubmitting?: boolean
  regenerationQuestionIds?: string[]
  regenerationMessage?: string
  regenerationError?: string
}>(), {
  issues: () => [],
  disabled: false,
  showRegenerationActions: false,
  canRegenerateBatched: false,
  canRegenerateWholeDocument: false,
  regenerationBusy: false,
  regenerationMode: null,
  regenerationSubmitting: false,
  regenerationQuestionIds: () => [],
  regenerationMessage: '',
  regenerationError: '',
})

const emit = defineEmits<{
  edit: [edit: ConfigEditorEdit]
  validity: [valid: boolean]
  regenerate: [mode: 'batched' | 'whole_document']
}>()
const root = ref<HTMLElement | null>(null)
const scoreErrors = ref<Record<string, string>>({})
const editingRowId = ref<string | null>(null)
const blockingIssues = computed(() => props.issues.filter((issue) => issue.severity === 'error'))
const warningIssues = computed(() => props.issues.filter((issue) => issue.severity !== 'error'))
const totalBlocked = computed(() => props.totalScore !== 100)
const batchedButtonLabel = computed(() => {
  const ids = props.regenerationQuestionIds.join('、')
  const base = ids ? `分批重新生成 ${ids}` : '分批重新生成'
  if (props.regenerationMode !== 'batched') return base
  return props.regenerationSubmitting
    ? `正在提交 ${ids || '被拦题目'}…`
    : `正在重新生成 ${ids || '被拦题目'}…`
})
const wholeDocumentButtonLabel = computed(() => {
  if (props.regenerationMode !== 'whole_document') return '整卷重新生成'
  return props.regenerationSubmitting ? '正在提交整卷…' : '正在重新生成整卷…'
})
const objectiveQuestionTypes = new Set([
  'choice',
  'fill_blank',
  'judgement',
  'true_false',
  'direct_answer',
])
const firstRowIds = computed(() => {
  const seen = new Set<string>()
  const first = new Set<string>()
  for (const row of props.rows) {
    const partKey = `${row.question_id}\u0000${row.part_id}`
    if (seen.has(partKey)) continue
    seen.add(partKey)
    first.add(row.row_id)
  }
  return first
})

function identity(row: ConfigEditorRow): string {
  return `${row.question_id} ${row.part_id} ${row.step_id}`
}

function isObjective(row: ConfigEditorRow): boolean {
  return objectiveQuestionTypes.has(row.question_type)
}

function compactPreview(value: string | readonly string[], fallback = '未填写'): string {
  const source = typeof value === 'string' ? value : value.join('\n')
  const compacted = source
    .replace(/\r\n?|\u2028|\u2029/g, '\n')
    .split(/\n+/)
    .map((part) => part.trim().replace(/^[；;]+|[；;]+$/g, ''))
    .filter(Boolean)
    .join('；')
    .replace(/[；;]+/g, '；')
    .replace(/[ \t]+/g, ' ')
    .trim()
  return compacted || fallback
}

function openEditor(rowId: string): void {
  if (props.disabled) return
  editingRowId.value = rowId
}

function closeEditor(rowId: string): void {
  const row = [...(root.value?.querySelectorAll<HTMLElement>('[data-row-id]') ?? [])]
    .find((candidate) => candidate.dataset.rowId === rowId)
  const activeElement = document.activeElement
  if (activeElement instanceof HTMLElement && row?.contains(activeElement)) activeElement.blur()
  editingRowId.value = null
  void nextTick(() => row?.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')?.focus())
}

function editorId(index: number): string {
  return `rubric-unit-editor-${index}`
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
  field: 'required_elements' | 'deduction_rules' | 'part_deduction_rules',
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
  | 'required_elements' | 'deduction_rules' | 'part_deduction_rules' | 'answer_only_max_score'
  | 'require_final_answer' | 'final_answer_rule'

function fieldForIssue(issue: ConfigEditorIssue): RenderedEditField | null {
  if (issue.field === 'score') return 'score'
  if (issue.field === 'accepted_answers') return 'accepted_answers'
  if (issue.field === 'standard_answer') return 'standard_answer'
  if (issue.field === 'required_elements') return 'required_elements'
  if (issue.field === 'deduction_rules') return 'deduction_rules'
  if (issue.field === 'part_deduction_rules') return 'part_deduction_rules'
  if (issue.field === 'answer_only_max_score') return 'answer_only_max_score'
  if (issue.field === 'require_final_answer') return 'require_final_answer'
  if (issue.field === 'final_answer_rule') return 'final_answer_rule'
  return null
}

const firstRowFields = new Set<RenderedEditField>([
  'standard_answer',
  'accepted_answers',
  'part_deduction_rules',
  'answer_only_max_score',
  'require_final_answer',
  'final_answer_rule',
])

async function focusIssue(issue: ConfigEditorIssue): Promise<void> {
  if (issue.row_id === null) return
  const field = fieldForIssue(issue)
  if (field === null) return
  const issueRow = props.rows.find((candidate) => candidate.row_id === issue.row_id)
  const targetRowId = firstRowFields.has(field) && issueRow && !firstRowIds.value.has(issue.row_id)
    ? props.rows.find((candidate) => candidate.question_id === issueRow.question_id
      && candidate.part_id === issueRow.part_id)?.row_id ?? issue.row_id
    : issue.row_id
  editingRowId.value = targetRowId
  await nextTick()
  const row = [...(root.value?.querySelectorAll<HTMLElement>('[data-row-id]') ?? [])]
    .find((candidate) => candidate.dataset.rowId === targetRowId)
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
      <div
        v-if="showRegenerationActions"
        class="rubric-ledger__regeneration"
        aria-label="重新生成评分依据"
      >
        <div>
          <strong>如果问题来自 AI 生成结果</strong>
          <p>可以直接在这里重新生成；当前正式评分依据会保留到新版本完整成功。操作会调用模型并可能产生费用。</p>
        </div>
        <div class="rubric-ledger__regeneration-actions">
          <button
            type="button"
            name="分批重新生成"
            :disabled="disabled || regenerationBusy || !canRegenerateBatched"
            @click="emit('regenerate', 'batched')"
          >{{ batchedButtonLabel }}</button>
          <button
            type="button"
            name="整卷重新生成"
            :disabled="disabled || regenerationBusy || !canRegenerateWholeDocument"
            @click="emit('regenerate', 'whole_document')"
          >{{ wholeDocumentButtonLabel }}</button>
        </div>
        <p
          v-if="!canRegenerateBatched && !canRegenerateWholeDocument"
          class="rubric-ledger__regeneration-note"
        >当前没有可重用的来源试卷，请先重新上传试卷。</p>
        <p v-if="regenerationMessage" class="rubric-ledger__regeneration-note" role="status">
          {{ regenerationMessage }}
        </p>
        <p v-if="regenerationError" class="rubric-ledger__regeneration-error">
          {{ regenerationError }}
        </p>
      </div>
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

    <div class="rubric-ledger__cards" aria-label="评分依据评分点卡片">
      <article
        v-for="(row, index) in rows"
        :key="row.row_id"
        class="rubric-unit-card"
        :class="{
          'rubric-unit-card--objective': isObjective(row),
          'rubric-unit-card--editing': editingRowId === row.row_id,
        }"
        :data-row-id="row.row_id"
      >
        <button
          v-show="editingRowId !== row.row_id"
          type="button"
          class="rubric-unit-card__preview"
          :aria-controls="editorId(index)"
          aria-expanded="false"
          :aria-label="`${identity(row)}，${row.score} 分，点击编辑`"
          :disabled="disabled"
          @click="openEditor(row.row_id)"
        >
          <span class="rubric-unit-card__preview-header">
            <span class="rubric-unit-card__identity">
              <strong>{{ row.question_id }}</strong>
              <span>{{ row.part_label }}</span>
              <span>{{ row.step_id }}</span>
            </span>
            <span class="rubric-unit-card__goal" :title="compactPreview(row.core_goal, '未提供评分点')">
              {{ compactPreview(row.core_goal, '未提供评分点') }}
            </span>
            <strong class="rubric-unit-card__score-badge">{{ row.score }} 分</strong>
          </span>
          <span class="rubric-unit-card__preview-fields">
            <span
              v-if="firstRowIds.has(row.row_id)"
              class="rubric-unit-card__preview-field"
            >
              <span>标准答案</span>
              <span :title="compactPreview(row.standard_answer)">{{ compactPreview(row.standard_answer) }}</span>
            </span>
            <span class="rubric-unit-card__preview-field">
              <span>关键步骤</span>
              <span :title="compactPreview(row.required_elements)">
                {{ compactPreview(row.required_elements) }}
              </span>
            </span>
            <span class="rubric-unit-card__preview-field">
              <span>扣分规则</span>
              <span :title="compactPreview(row.deduction_rules)">
                {{ compactPreview(row.deduction_rules) }}
              </span>
            </span>
            <span
              v-if="firstRowIds.has(row.row_id) && row.part_deduction_rules.length"
              class="rubric-unit-card__preview-field"
            >
              <span>小问统一扣分规则</span>
              <span :title="compactPreview(row.part_deduction_rules)">
                {{ compactPreview(row.part_deduction_rules) }}
              </span>
            </span>
          </span>
          <span class="rubric-unit-card__preview-footer">
            <span :title="compactPreview(row.match_rule, '按评分依据判定')">
              {{ compactPreview(row.match_rule, '按评分依据判定') }}
            </span>
            <strong>点击编辑</strong>
          </span>
        </button>

        <section
          v-show="editingRowId === row.row_id"
          :id="editorId(index)"
          class="rubric-unit-card__editor"
          :aria-label="`${identity(row)} 编辑区`"
          @keydown.esc.stop="closeEditor(row.row_id)"
        >
          <header class="rubric-unit-card__editor-header">
            <span class="rubric-unit-card__identity">
              <strong>{{ row.question_id }}</strong>
              <span>{{ row.part_label }}</span>
              <span>{{ row.step_id }}</span>
            </span>
            <span class="rubric-unit-card__goal" :title="compactPreview(row.core_goal, '未提供评分点')">
              {{ compactPreview(row.core_goal, '未提供评分点') }}
            </span>
            <label class="rubric-unit-card__score">
              <span>分值</span>
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
            </label>
            <button type="button" class="rubric-unit-card__done" @click="closeEditor(row.row_id)">
              收起编辑
            </button>
          </header>

          <div class="rubric-unit-card__fields">
            <label v-if="firstRowIds.has(row.row_id)">
              <span>标准答案</span>
              <textarea
                rows="3"
                data-edit-field="standard_answer"
                :aria-label="`${identity(row)} 标准答案`"
                :value="row.standard_answer"
                :disabled="disabled"
                @change="textEdit(row, 'standard_answer', $event)"
              />
            </label>
            <label>
              <span>关键步骤 / 得分证据</span>
              <textarea
                rows="3"
                data-edit-field="required_elements"
                :aria-label="`${identity(row)} 证据要求/关键步骤`"
                :value="row.required_elements.join('\n')"
                :disabled="disabled"
                @change="listEdit(row, 'required_elements', $event)"
              />
            </label>
            <label>
              <span>扣分规则</span>
              <textarea
                rows="3"
                data-edit-field="deduction_rules"
                :aria-label="`${identity(row)} 扣分规则`"
                :value="row.deduction_rules.join('\n')"
                :disabled="disabled"
                @change="listEdit(row, 'deduction_rules', $event)"
              />
            </label>
            <label v-if="firstRowIds.has(row.row_id)">
              <span>等价答案（每行一个）</span>
              <textarea
                rows="3"
                data-edit-field="accepted_answers"
                :aria-label="`${identity(row)} 等价答案`"
                :value="row.accepted_answers.join('\n')"
                :disabled="disabled"
                @change="acceptedEdit(row, $event)"
              />
            </label>
            <label v-if="firstRowIds.has(row.row_id)">
              <span>小问统一扣分规则</span>
              <textarea
                rows="3"
                data-edit-field="part_deduction_rules"
                :aria-label="`${row.question_id} ${row.part_id} 小问统一扣分规则`"
                :value="row.part_deduction_rules.join('\n')"
                :disabled="disabled"
                @change="listEdit(row, 'part_deduction_rules', $event)"
              />
            </label>
          </div>

          <div v-if="firstRowIds.has(row.row_id)" class="rubric-ledger__policy">
            <div class="rubric-ledger__policy-heading">
              <strong>评分单元策略</strong>
              <span>{{ compactPreview(row.match_rule, '按评分依据判定') }}</span>
            </div>
            <label class="rubric-ledger__policy-check">
              <input
                type="checkbox"
                data-edit-field="require_final_answer"
                :aria-label="`${row.question_id} ${row.part_id} 要求最终答案`"
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
                :aria-label="`${row.question_id} ${row.part_id} 仅答案最高分`"
                :value="row.answer_only_max_score ?? ''"
                :disabled="disabled"
                @change="policyNumberEdit(row, $event)"
              >
            </label>
            <label class="rubric-ledger__policy-rule">
              <span>最终答案规则</span>
              <textarea
                rows="2"
                data-edit-field="final_answer_rule"
                :aria-label="`${row.question_id} ${row.part_id} 最终答案规则`"
                :value="row.final_answer_rule"
                :disabled="disabled"
                @change="policyRuleEdit(row, $event)"
              />
            </label>
          </div>
          <small v-else class="rubric-unit-card__policy-note">
            本评分点沿用同一小问首个评分点的评分策略。
          </small>
        </section>
      </article>
    </div>
  </section>
</template>
