import type { LessonNode, SemesterMappingProposalRange } from '../../api/catalog'

export interface MappingReviewGroup {
  key: string
  label: string
  startUnit: number
  endUnit: number
  mappings: SemesterMappingProposalRange[]
}

export function isTextbookRole(role: string | null | undefined): boolean {
  return role === 'textbook'
}

export function textbookSectionGroupKey(
  lessonRef: string,
  lessonNodes: readonly LessonNode[],
): string {
  const byId = new Map(lessonNodes.map(node => [node.id, node]))
  let current = byId.get(lessonRef)
  const visited = new Set<string>()
  while (current) {
    if (visited.has(current.id)) break
    visited.add(current.id)
    if (current.node_type === 'section') return `section:${current.id}`
    const parentId = current.parent_id
    if (!parentId) break
    current = byId.get(parentId)
  }
  return `lesson:${lessonRef}`
}

export function buildMappingReviewGroups(
  mappings: readonly SemesterMappingProposalRange[],
  options: {
    textbook: boolean
    lessonNodes: readonly LessonNode[]
  },
): MappingReviewGroup[] {
  if (!options.textbook) {
    return [...mappings]
      .sort((left, right) => (
        left.start_unit - right.start_unit || left.end_unit - right.end_unit
      ))
      .map(item => ({
        key: `mapping:${item.mapping_id}`,
        label: '',
        startUnit: item.start_unit,
        endUnit: item.end_unit,
        mappings: [item],
      }))
  }

  const buckets = new Map<string, SemesterMappingProposalRange[]>()
  for (const item of mappings) {
    const key = textbookSectionGroupKey(item.lesson_ref, options.lessonNodes)
    const list = buckets.get(key)
    if (list) list.push(item)
    else buckets.set(key, [item])
  }

  return [...buckets.entries()]
    .map(([key, items]) => ({
      key,
      label: textbookGroupLabel(key, items, options.lessonNodes),
      startUnit: Math.min(...items.map(item => item.start_unit)),
      endUnit: Math.max(...items.map(item => item.end_unit)),
      mappings: items,
    }))
    .sort((left, right) => (
      left.startUnit - right.startUnit || left.endUnit - right.endUnit
    ))
}

export function pendingReviewCount(groups: readonly MappingReviewGroup[]): number {
  return groups.filter(group => groupHasPending(group)).length
}

export function groupHasPending(group: MappingReviewGroup): boolean {
  return group.mappings.some(item => item.decision === 'pending')
}

export function coveringReviewGroup(
  groups: readonly MappingReviewGroup[],
  page: number,
): MappingReviewGroup | null {
  const covering = groups.filter(group => (
    group.startUnit <= page
    && page <= group.endUnit
    && group.mappings.some(item => item.decision !== 'rejected')
  ))
  return covering.find(groupHasPending) ?? covering[0] ?? null
}

export function nextPendingReviewGroup(
  groups: readonly MappingReviewGroup[],
  afterEnd: number,
): MappingReviewGroup | null {
  const pending = groups.filter(groupHasPending)
  return pending.find(group => group.startUnit > afterEnd) ?? pending[0] ?? null
}

function textbookGroupLabel(
  key: string,
  items: readonly SemesterMappingProposalRange[],
  lessonNodes: readonly LessonNode[],
): string {
  if (key.startsWith('section:')) {
    const section = lessonNodes.find(node => node.id === key.slice('section:'.length))
    const lessonCount = new Set(items.map(item => item.lesson_ref)).size
    const title = section?.title?.trim() || '未识别小节'
    return lessonCount > 1 ? `${title} · ${lessonCount} 个课时共用` : title
  }
  const lesson = lessonNodes.find(node => node.id === items[0]?.lesson_ref)
  return lesson?.title?.trim() || '未识别课时'
}
