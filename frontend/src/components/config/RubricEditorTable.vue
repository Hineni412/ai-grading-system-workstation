<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { ConfigEditorEdit, ConfigEditorIssue, ConfigEditorRow } from '../../api/config-workspace'

const props = withDefaults(defineProps<{
  rows: ConfigEditorRow[]
  totalScore: number
  issues?: ConfigEditorIssue[]
  disabled?: boolean
  showRegenerationActions?: boolean
  canRegenerateBatched?: boolean
  regenerationBusy?: boolean
  regenerationMode?: 'batched' | null
  regenerationSubmitting?: boolean
  regenerationQuestionIds?: string[]
  regenerationMessage?: string
  regenerationError?: string
}>(), {
  issues: () => [],
  disabled: false,
  showRegenerationActions: false,
  canRegenerateBatched: false,
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
  regenerate: []
}>()
const root = ref<HTMLElement | null>(null)
const scoreErrors = ref<Record<string, string>>({})
const scoreDrafts = ref<Record<string, string>>({})
watch(() => props.rows, () => {
  for (const rowId of Object.keys(scoreDrafts.value)) {
    if (!scoreErrors.value[rowId]) delete scoreDrafts.value[rowId]
  }
}, { deep: true })
const editingRowId = ref<string | null>(null)
const choiceScore = ref<number | null>(null)
const fillQuestionScore = ref<number | null>(null)
const blockingIssues = computed(() => props.issues.filter((issue) => issue.severity === 'error'))
const warningIssues = computed(() => [
  ...props.issues.filter((issue) => issue.severity !== 'error' && !issue.code.startsWith('manual_')),
  ...scoreWarnings.value,
])
const totalWarning = computed(() => Math.abs(props.totalScore - 100) > 0.000001)
const scoreWarnings = computed<ConfigEditorIssue[]>(() => {
  const warnings: ConfigEditorIssue[] = []
  const warn = (code: string, message: string, rowId: string | null = null) => warnings.push({
    code, message: `${message}；可以按当前人工设置保存。`, severity: 'warning', row_id: rowId, field: 'score',
  })
  if (totalWarning.value) warn('manual_total_score', `当前总分为 ${props.totalScore} 分，与自动配分的 100 分不同`)
  const questions = new Map<string, ConfigEditorRow[]>()
  for (const row of props.rows) questions.set(row.question_id, [...(questions.get(row.question_id) ?? []), row])
  const objectiveScores = new Map<string, Set<number>>()
  for (const [id, rows] of questions) {
    const score = rows.reduce((sum, row) => sum + row.score, 0)
    if (score > 18) warn(`manual_question_score:${id}`, `${id} 共 ${score} 分，超过自动配分的单题 18 分上限`, rows[0]!.row_id)
    if (rows.some(row => row.score < 0 || !Number.isInteger(row.score))) {
      warn(`manual_step_score:${id}`, `${id} 含小数或负数分值，超出自动配分的非负整数规则`, rows[0]!.row_id)
    }
    if (isObjective(rows[0]!)) {
      const type = rows[0]!.question_type
      const scores = objectiveScores.get(type) ?? new Set<number>()
      scores.add(score)
      objectiveScores.set(type, scores)
    }
  }
  if ([...objectiveScores.values()].some(scores => scores.size > 1)) warn('manual_objective_scores', '同类客观题的分值不完全相同')
  return warnings
})
const batchedButtonLabel = computed(() => {
  const ids = props.regenerationQuestionIds.join('、')
  const base = ids ? `重新分析 ${ids}` : '重新分析被拦题目'
  if (props.regenerationMode !== 'batched') return base
  return props.regenerationSubmitting
    ? `正在提交 ${ids || '被拦题目'}…`
    : `正在重新分析 ${ids || '被拦题目'}…`
})
const objectiveQuestionTypes = new Set([
  'choice',
  'fill_blank',
  'judgement',
  'true_false',
  'direct_answer',
])
const choiceQuestionIds = computed(() => [...new Set(
  props.rows.filter((row) => row.question_type === 'choice').map((row) => row.question_id),
)])
const fillQuestionIds = computed(() => [...new Set(
  props.rows.filter((row) => row.question_type === 'fill_blank').map((row) => row.question_id),
)])
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
const scoringGroups = computed(() => {
  const groups = new Map<string, { key: string; title: string; rows: ConfigEditorRow[]; objective: boolean }>()
  for (const row of props.rows) {
    const objective = isObjective(row)
    const key = objective ? `objective:${row.question_type}` : `${row.question_id}:${row.part_id}`
    if (!groups.has(key)) groups.set(key, {
      key, objective,
      title: objective ? (row.question_type === 'choice' ? '选择题' : row.question_type === 'fill_blank' ? '填空题' : '客观题')
        : partTitle(row),
      rows: [],
    })
    groups.get(key)!.rows.push(row)
  }
  return [...groups.values()]
})

function partTitle(row: ConfigEditorRow): string {
  if (row.part_id === row.question_id || row.part_id === '整题') return row.question_id
  const number = row.part_id.match(/(?:^P|\(P?)(\d+)\)?$/)?.[1]
  return `${row.question_id} ${number ? `第${number}问` : row.part_label}`
}

function requiresProcess(row: ConfigEditorRow): boolean {
  return !isObjective(row) && !['short_answer_points', 'visual_construction'].includes(row.response_mode ?? '')
}

function identity(row: ConfigEditorRow): string {
  return `${row.question_id} ${row.part_id} ${row.step_id}`
}

function distributedScores(total: number, count: number): number[] {
  if (!Number.isInteger(total)) {
    const unit = total / count
    return Array.from({ length: count }, (_, index) => index === count - 1 ? total - unit * (count - 1) : unit)
  }
  const unit = Math.floor(total / count)
  return Array.from({ length: count }, (_, index) => unit + (index < total - unit * count ? 1 : 0))
}

function applyBulkScore(questionType: 'choice' | 'fill_blank', rawScore: number | null): void {
  const score = Number(rawScore)
  if (rawScore === null || !Number.isFinite(score) || props.disabled) return
  const ids = questionType === 'choice' ? choiceQuestionIds.value : fillQuestionIds.value
  for (const questionId of ids) {
    const rows = props.rows.filter((row) => (
      row.question_id === questionId && row.question_type === questionType
    ))
    const scores = distributedScores(score, rows.length)
    rows.forEach((row, index) => emit('edit', { row_id: row.row_id, score: scores[index] }))
  }
}

function isObjective(row: ConfigEditorRow): boolean {
  return row.response_mode === 'exact_objective' || objectiveQuestionTypes.has(row.question_type)
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

function editorId(index: string): string {
  return `rubric-unit-editor-${index}`
}

function validateScore(row: ConfigEditorRow, event: Event): number | null {
  const raw = (event.currentTarget as HTMLInputElement).value.trim()
  const value = Number(raw)
  if (!raw || !Number.isFinite(value)) {
    scoreErrors.value[row.row_id] = '请填写有效数字分值。'
    emit('validity', false)
    return null
  }
  delete scoreErrors.value[row.row_id]
  emit('validity', Object.keys(scoreErrors.value).length === 0)
  return value
}

function numberEdit(row: ConfigEditorRow, event: Event): void {
  scoreDrafts.value[row.row_id] = (event.currentTarget as HTMLInputElement).value
  const value = validateScore(row, event)
  if (value === null || value === row.score) return
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
    delete scoreErrors.value[`${row.row_id}:policy`]
    emit('validity', Object.keys(scoreErrors.value).length === 0)
    emit('edit', { row_id: row.row_id, answer_only_max_score: null })
    return
  }
  const value = Number(raw)
  if (!Number.isFinite(value)) {
    scoreErrors.value[`${row.row_id}:policy`] = '请填写有效数字分值。'
    emit('validity', false)
    return
  }
  delete scoreErrors.value[`${row.row_id}:policy`]
  emit('validity', Object.keys(scoreErrors.value).length === 0)
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

// —— 左侧大纲：分区导航与滚动高亮 ——
const issueRowIds = computed(() => new Set(
  props.issues.map((issue) => issue.row_id).filter((id): id is string => id !== null),
))
const activePartKey = ref('')
let partObserver: IntersectionObserver | undefined

function partElements(): HTMLElement[] {
  return [...(root.value?.querySelectorAll<HTMLElement>('[data-part-key]') ?? [])]
}

function scrollToGroup(key: string): void {
  const target = partElements().find((el) => el.dataset.partKey === key)
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activePartKey.value = key
}

function groupHasIssue(group: { rows: ConfigEditorRow[] }): boolean {
  return group.rows.some((row) => issueRowIds.value.has(row.row_id))
}

function observeParts(): void {
  partObserver?.disconnect()
  if (typeof IntersectionObserver === 'undefined') return
  const elements = partElements()
  if (elements.length === 0) return
  partObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        activePartKey.value = (entry.target as HTMLElement).dataset.partKey ?? ''
      }
    }
  }, { rootMargin: '-96px 0px -60% 0px', threshold: 0 })
  elements.forEach((el) => partObserver!.observe(el))
}

onMounted(async () => {
  await nextTick()
  observeParts()
})
watch(scoringGroups, async () => {
  await nextTick()
  observeParts()
})
onBeforeUnmount(() => partObserver?.disconnect())

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
    <div class="rubric-ledger__layout">
      <nav class="rubric-ledger__outline" aria-label="评分结构大纲">
        <button
          v-for="group in scoringGroups"
          :key="group.key"
          type="button"
          :class="{ 'is-active': activePartKey === group.key }"
          @click="scrollToGroup(group.key)"
        >
          <span class="rubric-ledger__outline-title">{{ group.title }}</span>
          <span
            v-if="groupHasIssue(group)"
            class="rubric-ledger__outline-dot"
            title="有需核对项"
            aria-label="有需核对项"
          >●</span>
          <span class="rubric-ledger__outline-score">
            {{ group.rows.reduce((sum, row) => sum + row.score, 0) }} 分
          </span>
        </button>
      </nav>
      <div class="rubric-ledger__main">
    <header class="rubric-ledger__toolbar">
      <h2 id="rubric-ledger-title" tabindex="-1">本场赋分</h2>
      <strong
        class="rubric-ledger__total"
        :class="{ 'rubric-ledger__total--warning': totalWarning }"
      >总分 {{ totalScore }} 分</strong>
      <div class="rubric-ledger__bulk-scores" aria-label="统一修改客观题分值">
        <label>
          <span>选择题每题</span>
          <input v-model.number="choiceScore" aria-label="选择题每题分值" type="number" step="any" :disabled="disabled || choiceQuestionIds.length === 0">
          <button type="button" :disabled="disabled || choiceQuestionIds.length === 0 || !Number.isFinite(choiceScore)" @click="applyBulkScore('choice', choiceScore)">
            应用到 {{ choiceQuestionIds.length }} 题
          </button>
        </label>
        <label>
          <span>填空题每题总分</span>
          <input v-model.number="fillQuestionScore" aria-label="填空题每题总分" type="number" step="any" :disabled="disabled || fillQuestionIds.length === 0">
          <button type="button" :disabled="disabled || fillQuestionIds.length === 0 || !Number.isFinite(fillQuestionScore)" @click="applyBulkScore('fill_blank', fillQuestionScore)">
            应用到 {{ fillQuestionIds.length }} 题
          </button>
        </label>
      </div>
      <details v-if="warningIssues.length" class="rubric-ledger__issues-toggle">
        <summary>{{ warningIssues.length }} 项请核对（不影响人工保存）</summary>
        <div class="rubric-ledger__issues rubric-ledger__issues--warning" role="status">
          <button
            v-for="issue in warningIssues"
            :key="`${issue.code}:${issue.row_id}:${issue.field}`"
            type="button"
            :data-issue-row-id="issue.row_id ?? undefined"
            :data-issue-field="issue.field"
            @click="focusIssue(issue)"
          >{{ issue.message }}</button>
        </div>
      </details>
    </header>

    <div v-if="blockingIssues.length" class="rubric-ledger__issues rubric-ledger__issues--blocking" role="alert">
      <strong>保存检查提示</strong>
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
          <p>只重新分析被拦题目；正式版本保留到新版本完整成功。操作会调用模型并可能产生费用。</p>
        </div>
        <div class="rubric-ledger__regeneration-actions">
          <button
            type="button"
            name="重新分析被拦题目"
            :disabled="disabled || regenerationBusy || !canRegenerateBatched"
            @click="emit('regenerate')"
          >{{ batchedButtonLabel }}</button>
        </div>
        <p
          v-if="!canRegenerateBatched"
          class="rubric-ledger__regeneration-note"
        >当前没有可重新分析的来源题目，请先修正总分或重新核对来源试卷。</p>
        <p v-if="regenerationMessage" class="rubric-ledger__regeneration-note" role="status">
          {{ regenerationMessage }}
        </p>
        <p v-if="regenerationError" class="rubric-ledger__regeneration-error">
          {{ regenerationError }}
        </p>
      </div>
    </div>

    <div class="rubric-ledger__cards" aria-label="评分依据评分点卡片">
      <section v-for="group in scoringGroups" :key="group.key" class="rubric-part" :class="{ 'rubric-part--objective': group.objective }" :aria-label="`${group.title}评分标准`" :data-part-key="group.key">
        <header class="rubric-part__heading">
          <h3>{{ group.title }}</h3>
          <span>{{ group.objective ? `${group.rows.length} 题` : `${group.rows.length} 个评分点` }} · 共 {{ group.rows.reduce((sum, row) => sum + row.score, 0) }} 分</span>
          <span v-if="!group.objective" class="rubric-part__mode">
            {{ requiresProcess(group.rows[0]!) ? '按完成程度给分' : group.rows[0]!.response_mode === 'visual_construction' ? '按作图成果给分' : '按项给分' }}
          </span>
        </header>
        <div v-if="group.objective" class="rubric-part__rules">
          <strong>答对得满分，答错得 0 分</strong><span>接受等价答案，不要求过程。</span>
        </div>
        <details v-else class="rubric-part__reference">
          <summary>参考解答与补充规则</summary>
          <div class="rubric-part__rules">
            <template v-if="requiresProcess(group.rows[0]!)">
              <strong>按完成程度给整数分</strong>
              <span>完整或等价完成得本块满分；部分完成保留有效成果，同一错误不重复扣分。</span>
              <span class="rubric-part__answer-cap">本小问只有正确答案、无有效过程：{{ group.rows[0]!.answer_only_max_score ?? 1 }} 分</span>
              <span>有有效过程或已完成的独立求值目标时，按对应块给分。</span>
            </template>
            <template v-else><strong>{{ group.rows[0]!.response_mode === 'visual_construction' ? '按作图成果评分' : '各答案项分别给分' }}</strong><span>不套用“仅答案 1 分”的过程题规则。</span></template>
            <span>{{ group.rows.every(row => row.allow_alternative_methods !== false) ? '允许其他正确解法。' : '部分评分块限定方法，同一方法的等价表达仍可得分。' }}</span>
          </div>
          <p v-if="group.rows[0]!.standard_answer"><strong>参考解答</strong>{{ group.rows[0]!.standard_answer }}</p>
          <p v-for="rule in group.rows[0]!.part_deduction_rules" :key="rule">{{ rule }}</p>
          <p v-if="group.rows[0]!.require_final_answer">{{ group.rows[0]!.final_answer_rule }}</p>
        </details>
        <div class="rubric-part__steps">
      <div class="rubric-part__col-head" aria-hidden="true">
        <span>{{ group.objective ? '题号' : '步骤' }}</span>
        <span>目标</span>
        <span v-if="group.objective">标准答案</span>
        <span>{{ group.objective ? '接受答案' : '得分依据' }}</span>
        <span v-if="!group.objective">具体扣分</span>
        <span>等价达成</span>
        <span>分值</span>
      </div>
      <article
        v-for="row in group.rows"
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
          :aria-controls="editorId(row.row_id)"
          aria-expanded="false"
          :aria-label="`${identity(row)}，${row.score} 分，点击编辑`"
          :disabled="disabled"
          @click="openEditor(row.row_id)"
        >
          <span class="rubric-unit-card__preview-header">
            <span class="rubric-unit-card__identity">
              <strong>{{ group.objective ? row.question_id : row.step_id }}</strong>
            </span>
            <span class="rubric-unit-card__goal" :title="compactPreview(row.core_goal, '未提供评分点')">
              {{ compactPreview(row.core_goal, '未提供评分点') }}
            </span>
            <strong class="rubric-unit-card__score-badge">{{ group.objective ? '' : '最高 ' }}{{ row.score }} 分</strong>
          </span>
          <span class="rubric-unit-card__preview-fields">
            <span
              v-if="group.objective && firstRowIds.has(row.row_id)"
              class="rubric-unit-card__preview-field rubric-field--answer"
            >
              <span class="sr-only">标准答案</span>
              <span :title="compactPreview(row.standard_answer)">{{ compactPreview(row.standard_answer) }}</span>
            </span>
            <span class="rubric-unit-card__preview-field rubric-field--required">
              <span class="sr-only">{{ group.objective ? '接受答案' : '得分依据' }}</span>
              <span :title="compactPreview(row.required_elements)">
                {{ compactPreview(row.required_elements) }}
              </span>
            </span>
            <span
              v-if="!group.objective && row.deduction_rules.length"
              class="rubric-unit-card__preview-field rubric-field--deduction"
            >
              <span class="sr-only">具体扣分</span>
              <span :title="compactPreview(row.deduction_rules)">
                {{ compactPreview(row.deduction_rules) }}
              </span>
            </span>
            <span
              v-if="row.equivalent_rules?.length"
              class="rubric-unit-card__preview-field rubric-field--equiv"
            >
              <span class="sr-only">等价达成</span>
              <span :title="compactPreview(row.equivalent_rules ?? [])">
                {{ compactPreview(row.equivalent_rules ?? []) }}
              </span>
            </span>
          </span>
        </button>

        <section
          v-show="editingRowId === row.row_id"
          :id="editorId(row.row_id)"
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
                step="any"
                data-edit-field="score"
                :aria-label="`${identity(row)} 分值`"
                :value="scoreDrafts[row.row_id] ?? row.score"
                :aria-invalid="scoreErrors[row.row_id] ? 'true' : 'false'"
                :disabled="disabled"
                @input="numberEdit(row, $event)"
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
              <span>{{ isObjective(row) ? '客观判定 · 答案正确得满分' : requiresProcess(row) ? '过程评分 · 按目标给整数部分分' : '成果评分 · 按实际完成情况给分' }}</span>
              <span v-if="row.match_rule">{{ row.match_rule }}</span>
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
            <label v-if="requiresProcess(row)">
              <span>仅答案最高分</span>
              <input
                type="number"
                step="any"
                data-edit-field="answer_only_max_score"
                :aria-label="`${row.question_id} ${row.part_id} 仅答案最高分`"
                :value="row.answer_only_max_score ?? ''"
                :aria-invalid="scoreErrors[`${row.row_id}:policy`] ? 'true' : 'false'"
                :disabled="disabled"
                @change="policyNumberEdit(row, $event)"
              >
              <small v-if="scoreErrors[`${row.row_id}:policy`]" class="rubric-ledger__field-error" role="alert">
                {{ scoreErrors[`${row.row_id}:policy`] }}
              </small>
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
    </div>
      </div>
    </div>
  </section>
</template>
