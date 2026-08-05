import { describe, expect, it } from 'vitest'

import { decodeCurriculumCatalog } from './question-bank'


describe('curriculum catalog decoder', () => {
  it('accepts the five-volume knowledge catalog response', () => {
    const volumes = Array.from({ length: 5 }, (_, index) => {
      const order = index + 1
      const id = `bnu24-math-volume-${order}`
      const label = `第${order}册`
      return {
        id,
        order,
        label,
        grade: '初中',
        semester: '上学期',
        textbook_version: '北师大版（2024）',
        source: {},
        statistics: {
          raw_nodes: 1,
          excluded_nodes: 0,
          retained_nodes: 1,
        },
        chapters: [{
          id: `${id}-chapter-1`,
          knowledge_id: `${id}-knowledge-1`,
          order: 1,
          number: '1',
          title: '第一章',
          label: '第一章',
          kind: 'chapter',
          display_name: `${label}｜第一章`,
          source_ref: {
            node_id: `${order}01`,
            relative_url: `/czsx/zj${order}01`,
          },
          exam_scope_values: [`${label} 第一章`],
          sections: [],
        }],
      }
    })
    const catalog = decodeCurriculumCatalog({
      schema_version: 2,
      catalog_id: 'bnu-math-2024-curriculum-v2',
      knowledge_standard_id: 'bnu-math-2024-curriculum-knowledge-v2',
      publisher: '北京师范大学出版社',
      subject: '初中数学',
      edition: '2024',
      statistics: {
        raw_nodes: 1186,
        excluded_nodes: 62,
        retained_nodes: 1124,
        chapters: 36,
        sections: 126,
        knowledge_points: 962,
      },
      volumes,
    })

    expect(catalog.volumes).toHaveLength(5)
    expect(catalog.statistics.retained_nodes).toBe(1124)
    expect(catalog.volumes.at(-1)?.id).toBe('bnu24-math-volume-5')
  })
})
