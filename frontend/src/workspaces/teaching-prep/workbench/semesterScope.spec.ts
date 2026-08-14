import { describe, expect, it } from 'vitest'

import type { CurriculumVolume } from '../../../api/question-bank'
import type { CurriculumEdition, TeachingSemester } from '../api/catalog'
import {
  defaultSchoolYear,
  matchPrepSemester,
  prepKeysForVolume,
} from './semesterScope'

function volume(overrides: Partial<CurriculumVolume> = {}): CurriculumVolume {
  return {
    id: 'g8-first',
    order: 1,
    label: '八年级上册',
    grade: '八年级',
    semester: '上册',
    textbook_version: '北师大版',
    source: {},
    statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
    chapters: [],
    ...overrides,
  }
}

function curriculum(overrides: Partial<CurriculumEdition> = {}): CurriculumEdition {
  return {
    id: 'c'.repeat(32),
    title: '八年级上册',
    grade_level: 8,
    volume: 'first',
    publisher: '北师大版',
    edition_label: '北师大版',
    revision: 1,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

function semester(overrides: Partial<TeachingSemester> = {}): TeachingSemester {
  return {
    id: 's'.repeat(32),
    curriculum_id: 'c'.repeat(32),
    curriculum_title: '八年级上册',
    school_year: '2026-2027',
    term: 'first',
    planned_new_lesson_count: 60,
    status: 'active',
    active_lesson_count: 0,
    not_started_lesson_count: 0,
    preparing_lesson_count: 0,
    ready_lesson_count: 0,
    taught_lesson_count: 0,
    skipped_lesson_count: 0,
    material_count: 0,
    parsed_material_count: 0,
    mapped_material_count: 0,
    revision: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

describe('prepKeysForVolume', () => {
  it('maps 八年级上册 to grade 8 first term', () => {
    expect(prepKeysForVolume(volume())).toMatchObject({
      gradeLevel: 8,
      volume: 'first',
      term: 'first',
      title: '八年级上册',
    })
  })

  it('maps 七年级下册 to grade 7 second term', () => {
    expect(prepKeysForVolume(volume({
      label: '七年级下册',
      grade: '七年级',
      semester: '下册',
    }))).toMatchObject({
      gradeLevel: 7,
      volume: 'second',
      term: 'second',
    })
  })
})

describe('defaultSchoolYear', () => {
  it('uses the current year as start from August', () => {
    expect(defaultSchoolYear(new Date('2026-08-14T00:00:00'))).toBe('2026-2027')
  })

  it('uses the previous year as start before August', () => {
    expect(defaultSchoolYear(new Date('2026-07-31T00:00:00'))).toBe('2025-2026')
  })
})

describe('matchPrepSemester', () => {
  it('does not match a second-term workspace to first-term volume', () => {
    const second = curriculum({
      id: 'd'.repeat(32),
      title: '验收·初二数学',
      volume: 'second',
    })
    const matched = matchPrepSemester(
      [second],
      [semester({
        id: 't'.repeat(32),
        curriculum_id: second.id,
        curriculum_title: second.title,
        term: 'second',
      })],
      volume(),
    )
    expect(matched).toBeNull()
  })

  it('prefers the active first-term workspace for the same grade and volume', () => {
    const archived = semester({
      id: 't'.repeat(32),
      status: 'archived',
      updated_at: '2026-06-01T00:00:00Z',
    })
    const active = semester({
      id: 'u'.repeat(32),
      status: 'active',
      updated_at: '2026-01-01T00:00:00Z',
    })
    const matched = matchPrepSemester(
      [curriculum()],
      [archived, active],
      volume(),
    )
    expect(matched?.id).toBe(active.id)
  })
})
