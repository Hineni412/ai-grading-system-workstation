import { normalizeGraphQuery, type GraphQueryInput } from '../../api/graph-query'

const STORAGE_KEY = 'ai-grading:personalized-paper-selection:v1'

export type ChapterGroupSort = 'size' | 'weakness' | 'similarity'

export interface ChapterGroupEditor {
  memberIds: string[]
  targetKeys: string[]
  scopeKeys: string[]
  original?: { memberIds: string[]; targetKeys: string[] }
}

export interface AdoptedChapterGroup {
  groupId: string
  memberIds: string[]
  targetKeys: string[]
  scopeKeys: string[]
  sourceVersion: string
}

export interface PaperSelectionSession {
  rulesVersion?: number
  targetKeys: string[]
  rangeKeys: string[]
  questionCount: number
  expectedMinutes?: number
  difficultyMin?: number
  difficultyMax: number
  trainingIntent?: 'remediation' | 'challenge'
  teachingProgressChapterId?: string
  excludeCurrentOriginals: boolean
  paperMode: 'individual' | 'shared'
  chapterKey?: string
  sectionKey?: string
  chapterScope?: GraphQueryInput
  groupSort?: ChapterGroupSort
  groupEditor?: ChapterGroupEditor | null
  adoptedGroup?: AdoptedChapterGroup | null
  arrangements?: AdoptedChapterGroup[]
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
      difficultyMax,
      excludeCurrentOriginals,
      paperMode,
    } = parsed
    if (
      !Array.isArray(targetKeys)
      || !targetKeys.every((key) => typeof key === 'string')
      || !Array.isArray(rangeKeys)
      || !rangeKeys.every((key) => typeof key === 'string')
      || typeof questionCount !== 'number'
      || !Number.isFinite(questionCount)
      || typeof difficultyMax !== 'number'
      || !Number.isFinite(difficultyMax)
      || typeof excludeCurrentOriginals !== 'boolean'
      || (paperMode !== 'individual' && paperMode !== 'shared')
    ) {
      return null
    }
    return {
      targetKeys,
      rangeKeys,
      questionCount,
      difficultyMax: parsed.rulesVersion === 5 ? difficultyMax : 7,
      rulesVersion: 5,
      excludeCurrentOriginals,
      paperMode,
      chapterKey: typeof parsed.chapterKey === 'string' ? parsed.chapterKey : '',
      trainingIntent: parsed.trainingIntent === 'challenge' ? 'challenge' : 'remediation',
      teachingProgressChapterId: typeof parsed.teachingProgressChapterId === 'string' ? parsed.teachingProgressChapterId : '',
      sectionKey: typeof parsed.sectionKey === 'string' ? parsed.sectionKey : '',
      chapterScope: parsed.chapterScope ? normalizeGraphQuery(parsed.chapterScope) : undefined,
      groupSort: parsed.groupSort === 'weakness' || parsed.groupSort === 'similarity' ? parsed.groupSort : 'size',
      groupEditor: validEditor(parsed.groupEditor) ? parsed.groupEditor : null,
      adoptedGroup: validGroup(parsed.adoptedGroup) ? parsed.adoptedGroup : null,
      arrangements: Array.isArray(parsed.arrangements) ? parsed.arrangements.filter(validGroup) : [],
    }
  } catch {
    return null
  }
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}
function validEditor(value: unknown): value is ChapterGroupEditor {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<ChapterGroupEditor>
  return strings(item.memberIds) && strings(item.targetKeys) && strings(item.scopeKeys)
    && (!item.original || (strings(item.original.memberIds) && strings(item.original.targetKeys)))
}
function validGroup(value: unknown): value is AdoptedChapterGroup {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<AdoptedChapterGroup>
  return typeof item.groupId === 'string' && typeof item.sourceVersion === 'string'
    && strings(item.memberIds) && strings(item.targetKeys) && strings(item.scopeKeys)
}

export function savePaperSelectionSession(selection: PaperSelectionSession): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify({ ...selection, rulesVersion: 5 }))
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}
