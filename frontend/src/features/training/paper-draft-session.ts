import { isRecord } from '../../api/validation'

const STORAGE_KEY = 'ai-grading:personalized-paper-draft:v1'

export interface PaperDraftSession {
  draftId: string
  requestToken?: string
  handoutRequestToken?: string
}

export function loadPaperDraftSession(): PaperDraftSession | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed) || typeof parsed.draftId !== 'string') return null
    const requestToken = typeof parsed.requestToken === 'string' ? parsed.requestToken : ''
    const handoutRequestToken = typeof parsed.handoutRequestToken === 'string' ? parsed.handoutRequestToken : ''
    if (!parsed.draftId && !/^[0-9a-f]{32}$/.test(requestToken)) return null
    return {
      draftId: parsed.draftId,
      requestToken: /^[0-9a-f]{32}$/.test(requestToken) ? requestToken : undefined,
      handoutRequestToken: /^[0-9a-f]{32}$/.test(handoutRequestToken) ? handoutRequestToken : undefined,
    }
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
