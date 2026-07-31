import type { QuickFragment } from './api/support'

export function mergeQuickFragmentWithNeighbor(
  fragments: QuickFragment[],
  sourceIndex: number,
): QuickFragment[] {
  if (
    fragments.length <= 1
    || sourceIndex < 0
    || sourceIndex >= fragments.length
  ) {
    return fragments.map((fragment) => ({ ...fragment }))
  }
  const targetIndex = sourceIndex === 0 ? 1 : sourceIndex - 1
  const firstIndex = Math.min(sourceIndex, targetIndex)
  const secondIndex = Math.max(sourceIndex, targetIndex)
  const mergedText = `${fragments[firstIndex]!.text}\n${fragments[secondIndex]!.text}`

  return fragments.flatMap((fragment, index) => {
    if (index === sourceIndex) return []
    if (index === targetIndex) {
      return [{ ...fragment, text: mergedText }]
    }
    return [{ ...fragment }]
  })
}
