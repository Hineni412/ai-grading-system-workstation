import type { LocationQuery, LocationQueryRaw } from 'vue-router'
import type { QuestionBankFacets, QuestionSkillIndex } from '../../api/question-bank'

export const TAG_DIMENSIONS = [
  { key: 'typeKeys', query: 'type', facet: 'task_types', label: '题型' },
  { key: 'knowledgePoints', query: 'kp', facet: 'knowledge_points', label: '知识点' },
  { key: 'abilities', query: 'ability', facet: 'abilities', label: '能力' },
  { key: 'thoughts', query: 'thought', facet: 'thoughts', label: '思想' },
  { key: 'methods', query: 'method', facet: 'methods', label: '方法' },
  { key: 'models', query: 'model', facet: 'models', label: '模型' },
  { key: 'specialTypes', query: 'special', facet: 'special_types', label: '特殊考法' },
  { key: 'errorPatternCategories', query: 'error', facet: 'error_pattern_categories', label: '错因' },
] as const satisfies readonly { key: string; query: string; facet: keyof QuestionBankFacets; label: string }[]

export type TagDimensionKey = typeof TAG_DIMENSIONS[number]['key']
export type TagSelection = Record<TagDimensionKey, string[]> & { sections: string[]; legacySkills: string[] }
export type Chapters = QuestionSkillIndex['chapters']

export function queryValues(query: LocationQuery, key: string): string[] {
  const values = query[key]
  return [...new Set((Array.isArray(values) ? values : [values]).filter((value): value is string => typeof value === 'string' && value.length > 0))]
}

export function parseTagSelection(query: LocationQuery): TagSelection {
  return {
    sections: queryValues(query, 'sec'), typeKeys: queryValues(query, 'type'),
    knowledgePoints: queryValues(query, 'kp'), abilities: queryValues(query, 'ability'),
    thoughts: queryValues(query, 'thought'), methods: queryValues(query, 'method'),
    models: queryValues(query, 'model'), specialTypes: queryValues(query, 'special'),
    errorPatternCategories: queryValues(query, 'error'), legacySkills: queryValues(query, 'skill').filter(key => key.startsWith('sk_')),
  }
}

export function selectionQuery(query: LocationQuery, selection: TagSelection): LocationQueryRaw {
  const next: LocationQueryRaw = { ...query, tab: 'tag', sec: selection.sections.length ? selection.sections : undefined,
    skill: selection.legacySkills.length ? selection.legacySkills : undefined }
  for (const dimension of TAG_DIMENSIONS) next[dimension.query] = selection[dimension.key].length ? selection[dimension.key] : undefined
  return next
}

export function normalizeChapters(chapters: Chapters, selection: readonly string[]): string[] {
  const picked = new Set(selection)
  for (const chapter of chapters) {
    if (picked.has(chapter.id) || (chapter.sections.length > 0 && chapter.sections.every(section => picked.has(section.id)))) {
      picked.add(chapter.id)
      for (const section of chapter.sections) picked.delete(section.id)
    }
  }
  return [...picked]
}

export function selectedSections(chapters: Chapters, selection: readonly string[]): Set<string> {
  const picked = new Set(selection)
  return new Set(chapters.flatMap(chapter => chapter.sections.filter(section => picked.has(chapter.id) || picked.has(section.id)).map(section => section.id)))
}

export function toggleChapter(chapters: Chapters, selection: readonly string[], id: string): string[] {
  const picked = new Set(normalizeChapters(chapters, selection))
  const chapter = chapters.find(chapter => chapter.id === id || chapter.sections.some(section => section.id === id))
  if (!chapter) return picked.has(id) ? [...picked].filter(value => value !== id) : [...picked, id]
  if (chapter.id === id) {
    const checked = picked.has(id)
    picked.delete(id)
    for (const section of chapter.sections) picked.delete(section.id)
    if (!checked) picked.add(id)
  } else if (picked.has(chapter.id)) {
    picked.delete(chapter.id)
    for (const section of chapter.sections) if (section.id !== id) picked.add(section.id)
  } else if (picked.has(id)) picked.delete(id)
  else picked.add(id)
  return normalizeChapters(chapters, [...picked])
}

export function chapterLabel(chapters: Chapters, id: string): string {
  for (const chapter of chapters) {
    if (chapter.id === id) return chapter.label
    const section = chapter.sections.find(section => section.id === id)
    if (section) return `${chapter.label} · ${section.label}`
  }
  return id
}

export function migrateTagQuery(query: LocationQuery, index: QuestionSkillIndex): { query: LocationQueryRaw; notice: string; changed: boolean } {
  const next: LocationQueryRaw = { ...query, tab: 'tag' }
  let notice = ''
  let changed = query.tab !== 'tag'
  if (queryValues(query, 'skill').includes('unlinked')) return { query: { ...query, tab: 'todo', skill: undefined }, notice, changed: true }
  const dimension = TAG_DIMENSIONS.find(dimension => dimension.key === queryValues(query, 'tagDim')[0])
  const legacyTags = queryValues(query, 'tag')
  if (dimension && legacyTags.length) next[dimension.query] = [...new Set([...queryValues(query, dimension.query), ...legacyTags])]
  const section = queryValues(query, 'section')[0]
  const oldScope = queryValues(query, 'scope')[0]
  if (section && (oldScope === 'section' || oldScope === 'chapter')) {
    const chapter = index.chapters.find(chapter => chapter.id === section || chapter.sections.some(item => item.id === section))
    const id = oldScope === 'chapter' ? chapter?.id : section
    if (id) next.sec = normalizeChapters(index.chapters, [...queryValues(query, 'sec'), id])
  }
  const topic = queryValues(query, 'topic')[0]
  if (topic) {
    const entry = index.chapters.flatMap(chapter => chapter.sections.flatMap(section => section.topics)).find(item => item.stable_key === topic)
    if (entry) next.kp = [...new Set([...queryValues(query, 'kp'), entry.filter_value])]
    else notice = '该知识点不在当前学期，已忽略旧链接中的知识点条件。'
  }
  for (const key of ['tagDim', 'tag', 'section', 'scope', 'topic']) {
    if (query[key] !== undefined) { delete next[key]; changed = true }
  }
  return { query: next, notice, changed }
}
