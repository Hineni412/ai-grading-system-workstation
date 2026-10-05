export function curriculumCatalog() {
  const volumes = [
    ['七年级', '上学期'],
    ['七年级', '下学期'],
    ['八年级', '上学期'],
    ['八年级', '下学期'],
    ['九年级', '上学期'],
  ] as const
  return {
    schema_version: 2,
    catalog_id: 'bnu-math-2024',
    knowledge_standard_id: 'bnu-math-2024-curriculum-knowledge-v2',
    publisher: '北京师范大学出版社',
    subject: '初中数学',
    edition: '2024',
    statistics: {
      raw_nodes: 5,
      excluded_nodes: 0,
      retained_nodes: 5,
      chapters: 5,
      sections: 0,
      knowledge_points: 0,
    },
    volumes: volumes.map(([grade, semester], index) => ({
      id: `volume-${index + 1}`,
      order: index + 1,
      label: `${grade}${semester === '上学期' ? '上册' : '下册'}`,
      grade,
      semester,
      textbook_version: '北师大版2024',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [{
        id: `chapter-${index + 1}`,
        knowledge_id: `chapter-${index + 1}`,
        order: 1,
        number: '第一章',
        title: `测试章节${index + 1}`,
        label: `第一章 测试章节${index + 1}`,
        kind: 'chapter',
        display_name: `${grade}${semester === '上学期' ? '上册' : '下册'}｜第一章 测试章节${index + 1}`,
        source_ref: {
          node_id: `node-${index + 1}`,
          relative_url: `/czsx/zj${index + 1}`,
        },
        exam_scope_values: [
          `${grade}${semester === '上学期' ? '上册' : '下册'} 测试范围${index + 1}`,
        ],
        sections: [],
      }],
    })),
  }
}
