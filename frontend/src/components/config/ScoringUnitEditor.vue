<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ConfigEditorCommand,
  ConfigEditorRow,
  ManualQuestionPartInput,
} from '../../api/config-workspace'

const props = withDefaults(defineProps<{
  questionId: string
  rows?: ConfigEditorRow[]
  disabled?: boolean
  scoreReviewRequired?: boolean
}>(), {
  rows: () => [],
  disabled: false,
  scoreReviewRequired: false,
})

const emit = defineEmits<{
  command: [command: ConfigEditorCommand]
  retry: [questionId: string]
}>()

const localParts = ref<ManualQuestionPartInput[]>([])
const validationError = ref('')
let nextPartNumber = 1
let nextStepNumber = 1
const expectedTotal = computed(() => props.rows.reduce((total, row) => total + row.score, 0))
const draftTotal = computed(() => localParts.value.reduce(
  (total, part) => total + part.steps.reduce((subtotal, step) => subtotal + Number(step.score || 0), 0),
  0,
))
const stepCount = computed(() => localParts.value.reduce((total, part) => total + part.steps.length, 0))
const manualValidationError = computed(() => {
  if (localParts.value.length === 0) return '至少保留一个小问。'
  if (localParts.value.some((part) => part.steps.length === 0)) return '每个小问至少保留一个步骤点。'
  const steps = localParts.value.flatMap((part) => part.steps)
  if (steps.some((step) => !Number.isFinite(step.score))) {
    return '每个步骤点都需要填写有效数字分值。'
  }
  if (steps.some((step) => !step.core_goal.trim())) return '每个步骤点都需要填写评分目标。'
  return ''
})

function resetParts(): void {
  const grouped = new Map<string, ManualQuestionPartInput>()
  for (const row of props.rows) {
    let part = grouped.get(row.part_id)
    if (!part) {
      part = { part_id: row.part_id, steps: [] }
      grouped.set(row.part_id, part)
    }
    part.steps.push({
      step_id: row.step_id === '未拆评分点' || row.step_id === '整题'
        ? `S${part.steps.length + 1}`
        : row.step_id,
      score: props.scoreReviewRequired ? 0 : row.score,
      core_goal: row.core_goal === '未拆评分点' || row.core_goal === '整题'
        ? ''
        : row.core_goal,
    })
  }
  localParts.value = grouped.size > 0
    ? [...grouped.values()]
    : [{ part_id: 'P1', steps: [{ step_id: 'S1', score: 0, core_goal: '' }] }]
  nextPartNumber = Math.max(0, ...localParts.value.map(part => Number(part.part_id.match(/P(\d+)/)?.[1] ?? 0))) + 1
  nextStepNumber = Math.max(0, ...localParts.value.flatMap(part => part.steps.map(step => Number(step.step_id.match(/^S(\d+)$/)?.[1] ?? 0)))) + 1
  validationError.value = ''
}

function addPart(): void {
  localParts.value.push({
    part_id: `P${nextPartNumber++}`,
    steps: [{ step_id: 'S1', score: 0, core_goal: '' }],
  })
}

function removePart(index: number): void {
  if (localParts.value.length <= 1) return
  localParts.value.splice(index, 1)
}

function addStep(partIndex: number): void {
  const part = localParts.value[partIndex]
  if (!part) return
  part.steps.push({ step_id: `S${nextStepNumber++}`, score: 0, core_goal: '' })
}

function removeStep(partIndex: number, stepIndex: number): void {
  const part = localParts.value[partIndex]
  if (!part || part.steps.length <= 1) return
  part.steps.splice(stepIndex, 1)
}

function partTotal(part: ManualQuestionPartInput): number {
  return part.steps.reduce((total, step) => total + Number(step.score || 0), 0)
}

function applyStructure(): void {
  if (manualValidationError.value) {
    validationError.value = manualValidationError.value
    return
  }
  validationError.value = ''
  emit('command', {
    kind: 'replace_question_structure',
    question_id: props.questionId,
    parts: localParts.value.map((part) => ({
      part_id: part.part_id,
      steps: part.steps.map((step) => ({
        step_id: step.step_id,
        score: Number(step.score),
        core_goal: step.core_goal.trim(),
      })),
    })),
  })
}

watch(
  () => [props.questionId, props.rows, props.scoreReviewRequired] as const,
  resetParts,
  { immediate: true, deep: true },
)
</script>

<template>
  <section class="scoring-unit-editor" :aria-labelledby="`scoring-unit-${questionId}`">
    <header class="scoring-unit-editor__header">
      <div>
        <h3 :id="`scoring-unit-${questionId}`">{{ questionId }} 解答题结构</h3>
        <span class="scoring-unit-editor__summary">{{ localParts.length }} 小问 · {{ stepCount }} 个步骤点</span>
      </div>
      <div class="scoring-unit-editor__total" :class="{ 'is-invalid': Boolean(manualValidationError) }">
        <span>本题合计</span>
        <strong>{{ draftTotal }} / {{ expectedTotal }} 分</strong>
      </div>
    </header>

    <p v-if="scoreReviewRequired" class="scoring-unit-editor__review" role="alert">
      AI 已只重试本题。请逐项确认步骤并重新赋分，保存本题结构后才能完成确认。
    </p>

    <div class="scoring-unit-editor__parts">
      <article v-for="(part, partIndex) in localParts" :key="partIndex" class="scoring-unit-editor__part">
        <header>
          <div class="scoring-unit-editor__part-title">
            <strong>第 {{ partIndex + 1 }} 小问</strong>
            <span>{{ part.steps.length }} 个步骤点 · {{ partTotal(part) }} 分</span>
          </div>
          <button
            type="button"
            :disabled="disabled || localParts.length <= 1"
            :aria-label="`删除第 ${partIndex + 1} 小问`"
            @click="removePart(partIndex)"
          >删除小问</button>
        </header>
        <div class="scoring-unit-editor__step-grid">
          <div v-for="(step, stepIndex) in part.steps" :key="stepIndex" class="scoring-unit-editor__step-card">
            <strong class="scoring-unit-editor__step-title">步骤 {{ stepIndex + 1 }}</strong>
            <label class="scoring-unit-editor__goal">
              <span class="sr-only">评分目标</span>
              <textarea
                v-model="step.core_goal"
                rows="1"
                :disabled="disabled"
                :aria-label="`${questionId} 第 ${partIndex + 1} 小问步骤 ${stepIndex + 1} 评分目标`"
                placeholder="输入这个步骤的得分条件"
              />
            </label>
            <label class="scoring-unit-editor__score">
              <span class="sr-only">分值</span>
              <input
                v-model.number="step.score"
                type="number"
                step="any"
                :disabled="disabled"
                :aria-label="`${questionId} 第 ${partIndex + 1} 小问步骤 ${stepIndex + 1} 分值`"
              >
              <span>分</span>
            </label>
            <button
              type="button"
              class="scoring-unit-editor__remove-step"
              :disabled="disabled || part.steps.length <= 1"
              :aria-label="`删除第 ${partIndex + 1} 小问步骤 ${stepIndex + 1}`"
              @click="removeStep(partIndex, stepIndex)"
            >×</button>
          </div>
          <button type="button" class="scoring-unit-editor__add-step" :disabled="disabled" @click="addStep(partIndex)">
            ＋ 添加步骤点
          </button>
        </div>
      </article>
    </div>

    <button type="button" class="scoring-unit-editor__add-part" :disabled="disabled" @click="addPart">
      ＋ 添加小问
    </button>
    <p v-if="validationError || manualValidationError" role="alert">
      {{ validationError || manualValidationError }}
    </p>
    <p v-else-if="Math.abs(draftTotal - expectedTotal) > 0.000001" class="scoring-unit-editor__warning" role="status">
      本题由 {{ expectedTotal }} 分调整为 {{ draftTotal }} 分，可以按当前人工设置保存。
    </p>
    <footer class="scoring-unit-editor__actions">
      <button
        type="button"
        name="单题AI重试"
        :disabled="disabled"
        @click="emit('retry', questionId)"
      >AI 只重试这道题</button>
      <button
        type="button"
        name="保存本题结构"
        :disabled="disabled || Boolean(manualValidationError)"
        @click="applyStructure"
      >保存本题结构</button>
    </footer>
  </section>
</template>
