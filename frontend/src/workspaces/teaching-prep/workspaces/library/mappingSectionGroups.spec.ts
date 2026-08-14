import { describe, expect, it } from 'vitest'

import type { LessonNode, SemesterMappingProposalRange } from '../../api/catalog'
import {
  buildMappingReviewGroups,
  coveringReviewGroup,
  pendingReviewCount,
  textbookSectionGroupKey,
} from './mappingSectionGroups'

const stamp = '2026-08-03T00:00:00Z'

function node(
  id: string,
  nodeType: LessonNode['node_type'],
  title: string,
  parentId: string | null = null,
): LessonNode {
  return {
    id,
    curriculum_id: 'c'.repeat(32),
    parent_id: parentId,
    node_type: nodeType,
    title,
    sort_order: 1,
    duration_minutes: nodeType === 'lesson' ? 45 : null,
    source_kind: 'teacher',
    is_active: true,
    revision: 1,
    created_at: stamp,
    updated_at: stamp,
  }
}

function mapping(
  mappingId: string,
  lessonRef: string,
  start: number,
  end: number,
): SemesterMappingProposalRange {
  return {
    mapping_id: mappingId,
    material_record_id: 'r'.repeat(32),
    lesson_ref: lessonRef,
    start_unit: start,
    end_unit: end,
    purpose: 'textbook',
    decision: 'pending',
    teacher_revision: null,
    decision_reason: null,
    basis: '目录',
  }
}

describe('mappingSectionGroups', () => {
  const sectionId = 's'.repeat(32)
  const lessonA = 'a'.repeat(32)
  const lessonB = 'b'.repeat(32)
  const lessonC = 'c'.repeat(32)
  const nodes = [
    node('h'.repeat(32), 'chapter', '第一章', null),
    node(sectionId, 'section', '1 探索勾股定理', 'h'.repeat(32)),
    node(lessonA, 'lesson', '第1课时', sectionId),
    node(lessonB, 'lesson', '第2课时', sectionId),
    node(lessonC, 'lesson', '第3课时', 'h'.repeat(32)),
  ]

  it('groups textbook lessons that share a section', () => {
    const groups = buildMappingReviewGroups(
      [
        mapping('m1', lessonA, 5, 7),
        mapping('m2', lessonB, 8, 10),
      ],
      { textbook: true, lessonNodes: nodes },
    )

    expect(groups).toHaveLength(1)
    expect(groups[0]?.key).toBe(`section:${sectionId}`)
    expect(groups[0]?.startUnit).toBe(5)
    expect(groups[0]?.endUnit).toBe(10)
    expect(groups[0]?.label).toContain('1 探索勾股定理')
    expect(groups[0]?.label).toContain('2 个课时共用')
    expect(pendingReviewCount(groups)).toBe(1)
  })

  it('keeps workbook mappings as one confirm unit each', () => {
    const groups = buildMappingReviewGroups(
      [
        mapping('m1', lessonA, 5, 6),
        mapping('m2', lessonB, 7, 8),
      ],
      { textbook: false, lessonNodes: nodes },
    )

    expect(groups).toHaveLength(2)
    expect(groups.map(item => item.key)).toEqual(['mapping:m1', 'mapping:m2'])
    expect(pendingReviewCount(groups)).toBe(2)
  })

  it('does not merge a lesson that only hangs under a chapter', () => {
    expect(textbookSectionGroupKey(lessonC, nodes)).toBe(`lesson:${lessonC}`)
  })

  it('prefers a pending section when a page is covered by more than one group', () => {
    const groups = buildMappingReviewGroups(
      [
        { ...mapping('m1', lessonA, 5, 10), decision: 'accepted' },
        mapping('m2', lessonC, 9, 12),
      ],
      { textbook: true, lessonNodes: nodes },
    )
    const covering = coveringReviewGroup(groups, 9)
    expect(covering?.key).toBe(`lesson:${lessonC}`)
  })
})
