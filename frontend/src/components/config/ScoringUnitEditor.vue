<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { ConfigEditorCommand, ManualPartInput } from '../../api/config-workspace'

const props = withDefaults(defineProps<{
  questionId: string
  parts?: ManualPartInput[]
  disabled?: boolean
}>(), {
  parts: () => [],
  disabled: false,
})

const emit = defineEmits<{
  command: [command: ConfigEditorCommand]
  refine: [command: ConfigEditorCommand]
}>()

const splitCount = ref(2)
const splitStyle = ref<'subquestion' | 'blank'>('subquestion')
const localParts = ref<ManualPartInput[]>([])
const validationError = ref('')
const manualValidationError = computed(() => {
  const ids = localParts.value.map((part) => part.part_id.trim())
  if (localParts.value.length === 0 || ids.some((id) => !id)) return '评分单元 ID 不能为空。'
  if (new Set(ids).size !== ids.length) return '同一题的评分单元 ID 不能重复。'
  if (localParts.value.some((part) => !Number.isFinite(part.score)
    || part.score <= 0 || part.score > 100)) {
    return '每个评分单元的分值必须大于 0 且不超过 100。'
  }
  if (localParts.value.some((part) => !part.core_goal.trim())) return '每个评分单元都必须填写评分目标。'
  return ''
})

function resetParts(): void {
  localParts.value = props.parts.length > 0
    ? props.parts.map((part) => ({ ...part }))
    : [{ part_id: 'P1', score: 0, core_goal: '' }, { part_id: 'P2', score: 0, core_goal: '' }]
  validationError.value = ''
}

function updatePart(index: number, field: keyof ManualPartInput, event: Event): void {
  const part = localParts.value[index]
  if (!part) return
  const raw = (event.currentTarget as HTMLInputElement).value
  localParts.value[index] = { ...part, [field]: field === 'score' ? Number(raw) : raw }
  validationError.value = ''
}

function addPart(): void {
  localParts.value.push({ part_id: `P${localParts.value.length + 1}`, score: 0, core_goal: '' })
  validationError.value = ''
}

function removePart(index: number): void {
  if (localParts.value.length <= 1) return
  localParts.value.splice(index, 1)
  validationError.value = ''
}

function splitCommand(): ConfigEditorCommand | null {
  if (!Number.isSafeInteger(splitCount.value) || splitCount.value < 2 || splitCount.value > 20) {
    validationError.value = '拆分数量必须为 2 至 20 的整数。'
    return null
  }
  validationError.value = ''
  return { kind: 'split', question_id: props.questionId, count: splitCount.value, style: splitStyle.value }
}

function replaceCommand(): ConfigEditorCommand | null {
  const parts = localParts.value.map((part) => ({
    part_id: part.part_id.trim(), score: part.score, core_goal: part.core_goal.trim(),
  }))
  if (manualValidationError.value) {
    validationError.value = manualValidationError.value
    return null
  }
  validationError.value = ''
  return { kind: 'replace_parts', question_id: props.questionId, parts }
}

function applySplit(): void {
  const command = splitCommand()
  if (command) emit('command', command)
}

function applyReplace(refine: boolean): void {
  const command = replaceCommand()
  if (!command) return
  if (refine) emit('refine', command)
  else emit('command', command)
}

watch(() => [props.questionId, props.parts] as const, resetParts, { immediate: true, deep: true })
</script>

<template>
  <section class="scoring-unit-editor" :aria-labelledby="`scoring-unit-${questionId}`">
    <header>
      <div>
        <h3 :id="`scoring-unit-${questionId}`">{{ questionId }} 评分单元</h3>
        <p>手工结构的 ID 会保持不变；AI 只能在这个结构内完善内容。</p>
      </div>
    </header>

    <fieldset :disabled="disabled" class="scoring-unit-editor__split">
      <legend>快速拆分</legend>
      <label>
        <span>数量</span>
        <input v-model.number="splitCount" type="number" min="2" max="20" :aria-label="`${questionId} 拆分数量`">
      </label>
      <label><input v-model="splitStyle" type="radio" value="subquestion" aria-label="按小问拆分"> 按小问</label>
      <label><input v-model="splitStyle" type="radio" value="blank" aria-label="按空格拆分"> 按空格</label>
      <button type="button" name="应用拆分" @click="applySplit">应用拆分</button>
    </fieldset>

    <div class="scoring-unit-editor__manual">
      <div class="scoring-unit-editor__manual-heading">
        <strong>手工评分单元</strong>
        <button type="button" :disabled="disabled" @click="addPart">添加评分单元</button>
      </div>
      <div v-for="(part, index) in localParts" :key="index" class="scoring-unit-editor__part">
        <label>
          <span>ID</span>
          <input :value="part.part_id" :aria-label="`${questionId} 第 ${index + 1} 个评分单元 ID`" :disabled="disabled" @input="updatePart(index, 'part_id', $event)">
        </label>
        <label>
          <span>分值</span>
          <input type="number" min="0.01" max="100" step="0.5" :value="part.score" :aria-label="`${questionId} ${part.part_id || index + 1} 分值`" :disabled="disabled" @input="updatePart(index, 'score', $event)">
        </label>
        <label>
          <span>评分目标</span>
          <input :value="part.core_goal" :aria-label="`${questionId} ${part.part_id || index + 1} 评分目标`" :disabled="disabled" @input="updatePart(index, 'core_goal', $event)">
        </label>
        <button type="button" :aria-label="`删除 ${questionId} ${part.part_id || index + 1}`" :disabled="disabled || localParts.length <= 1" @click="removePart(index)">删除</button>
      </div>
      <p v-if="validationError || manualValidationError" role="alert">
        {{ validationError || manualValidationError }}
      </p>
      <div class="scoring-unit-editor__actions">
        <button type="button" name="替换评分单元" :disabled="disabled || Boolean(manualValidationError)" @click="applyReplace(false)">替换评分单元</button>
        <button type="button" name="AI 完善评分单元" :disabled="disabled || Boolean(manualValidationError)" @click="applyReplace(true)">AI 完善评分单元</button>
      </div>
    </div>
  </section>
</template>
