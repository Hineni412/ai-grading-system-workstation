const STORAGE_KEY = 'ai-grading:personalized-paper-draft:v1'

export interface PaperDraftSession {
  fingerprint: string
  draftId: string
  requestToken?: string
}

export function loadPaperDraftSession(): PaperDraftSession | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PaperDraftSession>
    if (
      typeof parsed.fingerprint !== 'string'
      || typeof parsed.draftId !== 'string'
      || !parsed.fingerprint
      || (!parsed.draftId && !/^[0-9a-f]{32}$/.test(parsed.requestToken ?? ''))
    ) {
      return null
    }
    return { fingerprint: parsed.fingerprint, draftId: parsed.draftId, requestToken: parsed.requestToken }
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
