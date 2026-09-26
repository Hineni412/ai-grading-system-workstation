import { describe, expect, it } from 'vitest'

import { curriculumVolumeAbbrev } from '../curriculum-label'

describe('curriculumVolumeAbbrev', () => {
  it('compresses 年级/册 labels to grade + semester characters', () => {
    expect(curriculumVolumeAbbrev('八年级上册')).toBe('八上')
    expect(curriculumVolumeAbbrev('七年级下册')).toBe('七下')
    expect(curriculumVolumeAbbrev('九年级全册')).toBe('九全')
  })

  it('falls back to the first two characters for other labels', () => {
    expect(curriculumVolumeAbbrev('高中数学必修一')).toBe('高中')
    expect(curriculumVolumeAbbrev('综合练习')).toBe('综合')
  })

  it('returns empty for missing or blank labels', () => {
    expect(curriculumVolumeAbbrev(null)).toBe('')
    expect(curriculumVolumeAbbrev(undefined)).toBe('')
    expect(curriculumVolumeAbbrev('   ')).toBe('')
  })
})
