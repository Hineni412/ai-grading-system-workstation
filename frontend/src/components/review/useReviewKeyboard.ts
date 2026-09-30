import { nextTick, type Ref } from 'vue'

import { useReviewQueueStore } from '../../stores/review-queue'
import type { ReviewMode } from './useReviewStudentNav'

// 复核页快捷键：深查里方向键换题/换学生，批量页 j/k 换行、/ 聚焦搜索。
export function useReviewKeyboard(options: {
  mode: Ref<ReviewMode>
  reviewPage: Ref<HTMLElement | null>
  onQuestionNav: (direction: -1 | 1) => void
  onStudentNav: (direction: -1 | 1) => void
}) {
  const reviewStore = useReviewQueueStore()
  const { mode, reviewPage, onQuestionNav, onStudentNav } = options

  function isShortcutProtectedTarget(target: EventTarget | null): boolean {
    if (!(target instanceof Element)) return false
    if (target.closest('input, textarea, select, button')) return true
    // 面板分隔条自身的 ←/→ 用于调整宽度
    if (target.closest('.review-deep-workspace__divider')) return true
    if (target instanceof HTMLElement && target.isContentEditable) return true
    const editableRoot = target.closest<HTMLElement>('[contenteditable]')
    if (editableRoot === null) return false
    return editableRoot.getAttribute('contenteditable')?.trim().toLocaleLowerCase() !== 'false'
  }

  function focusSelectedBatchScore(): void {
    const reviewItemId = reviewStore.selectedReviewItemId
    if (reviewItemId === null) return
    const card = [...(reviewPage.value?.querySelectorAll<HTMLElement>(
      '[data-review-item-id]',
    ) ?? [])].find((entry) => entry.dataset.reviewItemId === reviewItemId)
    card?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus()
  }

  async function focusBatchScore(reviewItemId: string): Promise<void> {
    reviewStore.selectItem(reviewItemId)
    await nextTick()
    focusSelectedBatchScore()
  }

  function onKeydown(event: KeyboardEvent): void {
    if (
      !event.defaultPrevented
      && !event.altKey
      && !event.ctrlKey
      && !event.metaKey
      && !event.shiftKey
      && mode.value === 'deep'
      && (!isShortcutProtectedTarget(event.target)
        // 快捷给分输入框里的方向键仍用于切换题/学生
        || (event.target instanceof Element
          && event.target.closest('.review-quick-score') !== null))
      && !(
        event.target instanceof Element
        && event.target.closest('.review-evidence-viewer')
      )
      && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
    ) {
      // ←/→ 同一名学生的题间切换；↑/↓ 同一题的学生间切换。
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        onQuestionNav(event.key === 'ArrowLeft' ? -1 : 1)
      } else {
        onStudentNav(event.key === 'ArrowUp' ? -1 : 1)
      }
      event.preventDefault()
      return
    }
    if (
      !event.defaultPrevented
      && !event.altKey
      && !event.ctrlKey
      && !event.metaKey
      && !event.shiftKey
      && !isShortcutProtectedTarget(event.target)
      && (
        event.key.toLocaleLowerCase() === 'j'
        || event.key.toLocaleLowerCase() === 'k'
      )
    ) {
      reviewStore.moveSelection(event.key.toLocaleLowerCase() === 'j' ? 1 : -1)
      event.preventDefault()
      if (mode.value === 'batch') void nextTick(focusSelectedBatchScore)
      return
    }
    if (
      event.defaultPrevented
      || event.altKey
      || event.ctrlKey
      || event.metaKey
      || event.shiftKey
      || event.key !== '/'
      || mode.value !== 'batch'
      || isShortcutProtectedTarget(event.target)
    ) return
    reviewPage.value?.querySelector<HTMLInputElement>('#review-search')?.focus()
    event.preventDefault()
  }

  return { onKeydown, focusBatchScore }
}
