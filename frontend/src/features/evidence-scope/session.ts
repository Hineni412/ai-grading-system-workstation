import type { GraphQueryInput } from '../../api/graph-query'
import { normalizeGraphQuery } from '../../api/graph-query'

const STORAGE_KEY = 'p4-evidence-scope-v1'

export function semesterEvidenceQuery(scope: GraphQueryInput['scope'], volumeId: string | null): GraphQueryInput {
  return {
    scope: { ...scope, use_historical_fallback: false },
    exam_scope: { mode: 'semester', curriculum_volume_id: volumeId ?? '' },
  }
}

export function loadEvidenceScope(): GraphQueryInput | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return null
    return normalizeGraphQuery(JSON.parse(raw) as GraphQueryInput)
  } catch {
    return null
  }
}

export function saveEvidenceScope(query: GraphQueryInput): void {
  try {
    globalThis.localStorage?.setItem(
      STORAGE_KEY,
      JSON.stringify(normalizeGraphQuery(query)),
    )
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}
