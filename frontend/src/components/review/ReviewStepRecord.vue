<script setup lang="ts">
import { computed } from 'vue'
import { resolveReviewItem, type ReviewItemLike } from '../../api/review'
import { aiStepFor, previousFailedSteps, stepNumber, useReviewDraftStore, type ReviewDraft } from '../../stores/review-drafts'

const props = defineProps<{ item: ReviewItemLike; draft: ReviewDraft; index: number; disabled: boolean }>()
const store = useReviewDraftStore()
const step = computed(() => props.draft.stepScores![props.index]!)
const ai = computed(() => aiStepFor(props.item, step.value))
const saved = computed(() => {
  const item = resolveReviewItem(props.item)
  const review = item.metadata.teacher_review as { revision?: number; steps?: Record<string, unknown>[] } | undefined
  return item.teacher_locked && review?.revision === item.revision
    ? review.steps?.find((entry) => entry.part_id === step.value.partId && entry.step_id === step.value.stepId) : undefined
})
const previous = computed(() => previousFailedSteps(props.draft.stepScores!, props.index))
const sourceIndex = computed(() => props.draft.stepScores!.findIndex((entry) => entry.partId === step.value.partId && entry.stepId === step.value.carriedFrom))
const aiFailed = computed(() => saved.value?.deduction_source
  ? saved.value.deduction_source === 'ai'
  : typeof ai.value?.score_awarded === 'number' && ai.value.score_awarded < step.value.maxScore)
const record = computed(() => {
  if (step.value.scoreText === '') return '等待逐步给分'
  if (step.value.carriedFrom) return `沿用${stepNumber(sourceIndex.value)}的错误 · 不单独记错 · 方法按正确计`
  if (Number(step.value.scoreText) === step.value.maxScore) return aiFailed.value ? '改为达成 · 不计错因' : '达成 · 不计错因'
  if (aiFailed.value) return `错因沿用 AI：${saved.value?.missing_or_error || saved.value?.reason || ai.value?.missing_or_error || ai.value?.reason || '未提供具体说明'}`
  return ''
})
function toggleCarry(event: Event): void {
  store.updateStep(props.draft.key, props.index, { carryExplicit: true,
    carriedFrom: (event.target as HTMLInputElement).checked ? previous.value[previous.value.length - 1]?.stepId ?? null : null })
}
</script>

<template>
  <div class="review-step-record">
    <p v-if="step.recheck" class="review-step-record__recheck">{{ step.recheck }}</p>
    <p v-if="record" :title="record" class="review-step-record__text">保存后记录：{{ record }}</p>
    <label v-else class="review-step-record__note">
      <span>保存后记录：</span>
      <input :value="step.note" tabindex="-1" placeholder="人工复核扣分" aria-label="本步扣分原因"
        :disabled="disabled" @input="store.updateStep(draft.key, index, { note: ($event.target as HTMLInputElement).value })">
    </label>
    <div v-if="step.scoreText === '0' && previous.length" class="review-step-record__carry">
      <label><input type="checkbox" tabindex="-1" :checked="Boolean(step.carriedFrom)" :disabled="disabled" @change="toggleCarry">
        沿用前步错误（方法对，不单独记错）</label>
      <select v-if="step.carriedFrom" tabindex="-1" :value="step.carriedFrom" :disabled="disabled" aria-label="沿用的前序步骤"
        @change="store.updateStep(draft.key, index, { carryExplicit: true, carriedFrom: ($event.target as HTMLSelectElement).value })">
        <option v-for="entry in previous" :key="entry.stepId" :value="entry.stepId">
          {{ stepNumber(draft.stepScores!.indexOf(entry)) }} {{ entry.stepId }}
        </option>
      </select>
    </div>
  </div>
</template>
