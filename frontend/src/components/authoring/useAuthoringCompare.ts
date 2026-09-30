import { ref, type Ref } from 'vue'

import {
  authoringApi,
  type AuthoringContent,
  type AuthoringWorkDetail,
} from '../../api/authoring'
import { CONTENT_FIELD_LABELS, formatField } from './authoring-draft'

export interface CompareRow {
  key: string
  label: string
  a: string
  b: string
  changed: boolean
}

export function useAuthoringCompare(detail: Ref<AuthoringWorkDetail | null>) {
  const compareA = ref<number | null>(null)
  const compareB = ref<number | null>(null)
  const versionCache = new Map<number, AuthoringContent>()
  const compareRows = ref<CompareRow[]>([])
  const compareLoading = ref(false)

  function reset(): void {
    compareA.value = null
    compareB.value = null
    versionCache.clear()
  }

  async function runCompare(): Promise<void> {
    const work = detail.value
    const a = compareA.value
    const b = compareB.value
    if (!work || a === null || b === null || a === b) {
      compareRows.value = []
      return
    }
    compareLoading.value = true
    try {
      for (const versionNo of [a, b]) {
        if (!versionCache.has(versionNo)) {
          versionCache.set(
            versionNo,
            (await authoringApi.getVersion(work.work_id, versionNo)).content,
          )
        }
      }
      const contentA = versionCache.get(a) ?? {}
      const contentB = versionCache.get(b) ?? {}
      const keys = [...new Set([...Object.keys(contentA), ...Object.keys(contentB)])]
        .filter((key) => key !== 'figure_asset_ids')
      compareRows.value = keys.map((key) => {
        const textA = formatField(contentA[key])
        const textB = formatField(contentB[key])
        return {
          key,
          label: CONTENT_FIELD_LABELS[key] ?? key,
          a: textA,
          b: textB,
          changed: textA !== textB,
        }
      })
    } finally {
      compareLoading.value = false
    }
  }

  return { compareA, compareB, compareRows, compareLoading, runCompare, reset }
}
