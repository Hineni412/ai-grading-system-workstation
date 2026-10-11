import { isRecord } from '../../api/validation'

const STORAGE_KEY = 'ai-grading:personalized-paper-draft:v1'

export interface PaperDraftSession {
  draftId: string
}

export function loadPaperDraftSession(): PaperDraftSession | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed) || typeof parsed.draftId !== 'string' || !parsed.draftId) return null
    return { draftId: parsed.draftId }
  } catch {
    return null
  }
}

export function savePaperDraftSession(session: PaperDraftSession): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(session))
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

export function clearPaperDraftSession(): void {
  try {
    globalThis.localStorage?.removeItem(STORAGE_KEY)
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}
