const STORAGE_KEY = 'ai-grading:personalized-paper-selection:v1'

export interface PaperSelectionSession {
  targetKeys: string[]
  rangeKeys: string[]
  questionCount: number
  expectedMinutes: number
  difficultyMin: number
  difficultyMax: number
  excludeCurrentOriginals: boolean
  directRatio: number
  prerequisiteRatio: number
  transferRatio: number
  paperMode: 'individual' | 'shared'
}

// 出卷勾选与设置持久化到本机：与草稿记录同寿命，
// 关掉浏览器再打开也能恢复选择并接上草稿指纹。
export function loadPaperSelectionSession(): PaperSelectionSession | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PaperSelectionSession>
    const {
      targetKeys,
      rangeKeys,
      questionCount,
      expectedMinutes,
      difficultyMin,
      difficultyMax,
      excludeCurrentOriginals,
      directRatio,
      prerequisiteRatio,
      transferRatio,
      paperMode,
    } = parsed
    if (
      !Array.isArray(targetKeys)
      || !targetKeys.every((key) => typeof key === 'string')
      || !Array.isArray(rangeKeys)
      || !rangeKeys.every((key) => typeof key === 'string')
      || typeof questionCount !== 'number'
      || !Number.isFinite(questionCount)
      || typeof expectedMinutes !== 'number'
      || !Number.isFinite(expectedMinutes)
      || typeof difficultyMin !== 'number'
      || !Number.isFinite(difficultyMin)
      || typeof difficultyMax !== 'number'
      || !Number.isFinite(difficultyMax)
      || typeof excludeCurrentOriginals !== 'boolean'
      || typeof directRatio !== 'number'
      || !Number.isFinite(directRatio)
      || typeof prerequisiteRatio !== 'number'
      || !Number.isFinite(prerequisiteRatio)
      || typeof transferRatio !== 'number'
      || !Number.isFinite(transferRatio)
      || (paperMode !== 'individual' && paperMode !== 'shared')
    ) {
      return null
    }
    return {
      targetKeys,
      rangeKeys,
      questionCount,
      expectedMinutes,
      difficultyMin,
      difficultyMax,
      excludeCurrentOriginals,
      directRatio,
      prerequisiteRatio,
      transferRatio,
      paperMode,
    }
  } catch {
    return null
  }
}

export function savePaperSelectionSession(selection: PaperSelectionSession): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(selection))
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}
