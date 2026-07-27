<script setup lang="ts">
import { resolveReviewItem, type ReviewItemLike } from '../../api/review'
import StatePanel from '../design-system/StatePanel.vue'
import StatusBadge from '../design-system/StatusBadge.vue'

defineProps<{
  item: ReviewItemLike | null
  questionId: string | null
}>()

function confidenceLabel(item: ReviewItemLike): string {
  const resolved = resolveReviewItem(item)
  if (resolved.score_source !== 'ai') return '人工评分'
  return resolved.confidence_score === null
    ? '置信度未提供'
    : `置信度 ${Math.round(
      resolved.confidence_score <= 1
        ? resolved.confidence_score * 100
        : resolved.confidence_score,
    )}%`
}

function scoreLabel(value: number | null): string {
  return value === null ? '未填写' : String(value)
}

function statusLabel(item: ReviewItemLike): string {
  const resolved = resolveReviewItem(item)
  return {
    ungraded: '未批',
    ai_ready: 'AI 已完成',
    ai_review: 'AI 待复核',
    teacher_final: '教师已确认',
    failed: '处理失败',
  }[resolved.score_status]
}

function statusTone(
  item: ReviewItemLike,
): 'neutral' | 'ai' | 'warning' | 'teacher' | 'danger' {
  const resolved = resolveReviewItem(item)
  return {
    ungraded: 'neutral',
    ai_ready: 'ai',
    ai_review: 'warning',
    teacher_final: 'teacher',
    failed: 'danger',
  }[resolved.score_status] as 'neutral' | 'ai' | 'warning' | 'teacher' | 'danger'
}
</script>

<template>
  <section
    class="review-selection-summary review-selection-summary--compact"
    :aria-labelledby="item ? 'review-selection-title' : undefined"
  >
    <StatePanel
      v-if="!item"
      kind="empty"
      title="当前筛选没有记录"
      description="可以调整搜索词或复核范围。"
    />

    <template v-else>
      <header>
        <p v-if="questionId">题目 {{ questionId }}</p>
        <h2 id="review-selection-title">{{ item.student_name }}</h2>
        <p>{{ item.student_code ?? '未提供学号' }}</p>
        <p v-if="item.class_name">{{ item.class_name }}</p>
      </header>

      <p>当前得分 {{ scoreLabel(item.score_awarded) }} / {{ item.max_score }}</p>
      <p>{{ confidenceLabel(item) }}</p>
      <StatusBadge
        :tone="statusTone(item)"
        :label="statusLabel(item)"
      />

      <dl v-if="item.error_summary || item.error_category || item.deduction_reason">
        <template v-if="item.error_summary">
          <dt>错误摘要</dt>
          <dd>{{ item.error_summary }}</dd>
        </template>
        <template v-if="item.error_category">
          <dt>错误类别</dt>
          <dd>{{ item.error_category }}</dd>
        </template>
        <template v-if="item.deduction_reason">
          <dt>扣分原因</dt>
          <dd>{{ item.deduction_reason }}</dd>
        </template>
      </dl>

      <p class="review-selection-summary__notice">
        答卷原图不会被改写；分数只有在右侧完成教师确认后才会保存。
      </p>
    </template>
  </section>
</template>
