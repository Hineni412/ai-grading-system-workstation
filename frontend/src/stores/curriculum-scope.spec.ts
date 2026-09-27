import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { CurriculumCatalog, CurriculumVolume } from '../api/question-bank'
import {
  CURRICULUM_SCOPE_STORAGE_KEY,
  useCurriculumScopeStore,
} from './curriculum-scope'

function volume(id: string, order: number): CurriculumVolume {
  return {
    id,
    order,
    label: order === 1 ? '七年级上册' : '八年级上册',
    grade: order === 1 ? '七年级' : '八年级',
    semester: '上学期',
    textbook_version: '人教版',
    source: {},
    statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
    chapters: [],
  }
}

function catalog(volumes: CurriculumVolume[]): CurriculumCatalog {
  return {
    schema_version: 2,
    catalog_id: 'catalog',
    knowledge_standard_id: 'standard',
    publisher: '人民教育出版社',
    subject: '数学',
    edition: '人教版',
    statistics: {
      raw_nodes: 0,
      excluded_nodes: 0,
      retained_nodes: 0,
      chapters: 0,
      sections: 0,
      knowledge_points: 0,
    },
    volumes,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('global curriculum scope', () => {
  it('starts unselected, persists a teacher selection and restores it', async () => {
    const first = useCurriculumScopeStore()
    await first.initialize(async () => catalog([volume('g8-first', 2), volume('g7-first', 1)]))
    expect(first.selectedVolumeId).toBeNull()
    expect(first.volumes.map(item => item.id)).toEqual(['g7-first', 'g8-first'])

    first.selectVolume('g8-first')
    expect(localStorage.getItem(CURRICULUM_SCOPE_STORAGE_KEY)).toBe('g8-first')

    setActivePinia(createPinia())
    const restored = useCurriculumScopeStore()
    await restored.initialize(async () => catalog([volume('g8-first', 2)]))
    expect(restored.selectedVolumeId).toBe('g8-first')
  })

})
