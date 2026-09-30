import { computed, ref, type Ref } from 'vue'

import {
  confirmReviewItems,
  fetchReviewItems,
  resolveReviewItem,
  type ReviewConfirmInput,
  type ReviewItemLike,
} from '../../api/review'

export interface AnnotationRetryEntry {
  input: ReviewConfirmInput
  item: ReviewItemLike
}

export type ReviewFeedbackTone = 'success' | 'warning' | 'error'

export function useReviewAnnotationRetry(options: {
  batchSubmitting: Ref<boolean>
  feedback: Ref<string>
  feedbackTone: Ref<ReviewFeedbackTone>
}) {
  const { batchSubmitting, feedback, feedbackTone } = options
  const annotationRetryEntries = ref<AnnotationRetryEntry[]>([])

  const annotationRetryTitle = computed(() =>
    `分数已保存，${annotationRetryEntries.value.length} 份标注图需要重试`,
  )

  function annotationRetryKey(entry: AnnotationRetryEntry): string {
    return resolveReviewItem(entry.item).review_item_id
  }

  function mergeAnnotationRetryEntries(entries: AnnotationRetryEntry[]): void {
    const merged = new Map(
      annotationRetryEntries.value.map((entry) => [annotationRetryKey(entry), entry]),
    )
    for (const entry of entries) merged.set(annotationRetryKey(entry), entry)
    annotationRetryEntries.value = [...merged.values()]
  }

  function retryEntriesFromResponse(
    response: Awaited<ReturnType<typeof confirmReviewItems>>,
    inputs: ReviewConfirmInput[],
    items: ReviewItemLike[],
  ): AnnotationRetryEntry[] {
    const retryResultIds = new Set(
      response.annotation_outcomes
        .filter((outcome) => outcome.status === 'retry_required')
        .map((outcome) => outcome.result_id),
    )
    return inputs.flatMap((input, index) => {
      const item = items[index]
      if (
        item === undefined
        || item.result_id === null
        || !retryResultIds.has(item.result_id)
      ) return []
      return [{ input, item }]
    })
  }

  async function retryAnnotations(): Promise<void> {
    if (batchSubmitting.value || annotationRetryEntries.value.length === 0) return
    const entries = [...annotationRetryEntries.value]
    const snapshotByKey = new Map(
      entries.map((entry) => [annotationRetryKey(entry), entry]),
    )
    batchSubmitting.value = true
    let hadRequestFailure = false
    const remainingKeys = new Set<string>()

    try {
      const groups = new Map<string, AnnotationRetryEntry[]>()
      for (const entry of entries) {
        const key = `${entry.item.session_id}:${entry.item.question_id}`
        groups.set(key, [...(groups.get(key) ?? []), entry])
      }

      for (const group of groups.values()) {
        const first = group[0]!
        try {
          const currentItems = await fetchReviewItems(
            first.item.session_id,
            first.item.question_id,
            { scope: 'all' },
          )
          const currentById = new Map(
            currentItems.map(resolveReviewItem).map(
              (item) => [item.review_item_id, item],
            ),
          )
          const refreshedEntries = group.flatMap((entry) => {
            const current = currentById.get(annotationRetryKey(entry))
            if (!current) {
              remainingKeys.add(annotationRetryKey(entry))
              hadRequestFailure = true
              return []
            }
            return [{
              item: current,
              input: {
                ...entry.input,
                review_item_id: current.review_item_id,
                expected_revision: current.revision,
                student_id: current.student_id,
                result_id: current.result_id,
                detail_id: current.detail_id,
              },
            }]
          })
          if (refreshedEntries.length === 0) continue

          const response = await confirmReviewItems(
            first.item.session_id,
            first.item.question_id,
            refreshedEntries.map((entry) => entry.input),
          )
          const retryResultIds = new Set(
            response.annotation_outcomes
              .filter((outcome) => outcome.status === 'retry_required')
              .map((outcome) => outcome.result_id),
          )
          for (const entry of refreshedEntries) {
            if (
              entry.item.result_id !== null
              && retryResultIds.has(entry.item.result_id)
            ) {
              remainingKeys.add(entry.item.review_item_id)
            }
          }
        } catch {
          hadRequestFailure = true
          for (const entry of group) remainingKeys.add(annotationRetryKey(entry))
        }
      }

      annotationRetryEntries.value = annotationRetryEntries.value.filter((entry) => {
        const key = annotationRetryKey(entry)
        const snapshotEntry = snapshotByKey.get(key)
        return snapshotEntry !== entry || remainingKeys.has(key)
      })
      const remainingCount = annotationRetryEntries.value.length
      feedbackTone.value =
        hadRequestFailure ? 'error' : remainingCount > 0 ? 'warning' : 'success'
      feedback.value = hadRequestFailure
        ? '部分标注图重试失败；分数仍已保存，可以稍后再次重试。'
        : remainingCount > 0
          ? '分数保持已保存；仍有标注图需要再次重试。'
          : '标注图已重新生成。'
    } finally {
      batchSubmitting.value = false
    }
  }

  function handleDeepAnnotationRetry(entry: AnnotationRetryEntry): void {
    mergeAnnotationRetryEntries([entry])
    feedbackTone.value = 'warning'
    feedback.value = '分数已保存；标注图需要显式重试。'
  }

  return {
    annotationRetryEntries,
    annotationRetryTitle,
    mergeAnnotationRetryEntries,
    retryEntriesFromResponse,
    retryAnnotations,
    handleDeepAnnotationRetry,
  }
}
