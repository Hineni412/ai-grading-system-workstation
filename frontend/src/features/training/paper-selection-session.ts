import { normalizeGraphQuery, type GraphQueryInput } from '../../api/graph-query'
import type { CurriculumVolume } from '../../api/question-bank'
import type { TrainingDiagnosis } from '../../api/training'

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
  scopeMode?: 'comprehensive' | 'focused'
  rulesVersion?: number
  targetKeys: string[]
  rangeKeys: string[]
  purpose?: 'training' | 'handout'
  maxQuestionsPerSkill?: number
  maxWrittenQuestions?: number
  recentActivityCount?: number
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
      scopeMode: parsed.scopeMode === 'focused' ? 'focused' : 'comprehensive',
      targetKeys,
      rangeKeys,
      questionCount,
      purpose: parsed.purpose === 'handout' ? 'handout' : 'training',
      maxQuestionsPerSkill: parsed.maxQuestionsPerSkill ?? 1,
      maxWrittenQuestions: parsed.maxWrittenQuestions ?? 2,
      recentActivityCount: parsed.recentActivityCount ?? 3,
      difficultyMax: (parsed.rulesVersion ?? 0) >= 7 ? difficultyMax : parsed.rulesVersion === 6 ? Math.min(8, difficultyMax) : 8,
      rulesVersion: 7,
      excludeCurrentOriginals: true,
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

export function resolvePaperScope(volume: CurriculumVolume | null, diagnosis: TrainingDiagnosis | null,
  rangeKeys: string[], progressId: string, mode: 'comprehensive' | 'focused') {
  const parents = new Map((diagnosis?.knowledge_catalog ?? []).map(node => [node.knowledge_key, node.parent_knowledge_key]))
  const observed = new Set(rangeKeys)
  for (const student of mode === 'comprehensive' ? diagnosis?.students ?? [] : []) {
    for (const point of student.weak_points) {
      if (!point.source_question_refs?.length && !point.evidence_count) continue
      let key: string | null | undefined = point.knowledge_key
      const visited = new Set<string>()
      while (key && !visited.has(key)) { visited.add(key); observed.add(key); key = parents.get(key) }
      if (point.parent_knowledge_key) observed.add(point.parent_knowledge_key)
    }
  }
  const chapters = [...(volume?.chapters ?? [])].sort((a, b) => a.order - b.order)
  const progress = progressId ? chapters.find(c => c.id === progressId) : chapters.filter(c =>
    [...observed].some(key => key === c.knowledge_id || key.startsWith(c.knowledge_id + '_'))).pop()
  const selected = mode === 'focused' ? rangeKeys : chapters.filter(c => progress && c.order <= progress.order).map(c => c.knowledge_id)
  return { keys: selected, progressId: progress?.id ?? '',
    label: mode === 'focused' ? `专项训练 · ${rangeKeys.length} 个所选范围`
      : progress ? `综合训练 · 本册开头至${progress.label}` : '综合训练 · 请设置已学到的章节' }
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
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify({ ...selection, rulesVersion: 7 }))
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}
