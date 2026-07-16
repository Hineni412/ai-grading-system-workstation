import { describe, expect, it } from 'vitest'

import type { GraphNode, GraphRow } from '../api/graph'
import {
  buildEvidenceLaneModel,
  buildGroupingTree,
  masteryBand,
  nodeEvidenceSize,
  summarizeGraph,
} from '../features/knowledge-graph/model'

function node(label: string, mastery: number, itemCount = 1): GraphNode {
  return {
    knowledge_key: `knowledge_point:${label}`,
    knowledge_label: label,
    student_count: 1,
    item_count: itemCount,
    deduction_count: mastery < 1 ? 1 : 0,
    average_mastery: mastery,
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
  }
}

function row(studentId: number, code: string, name: string, label: string): GraphRow {
  return {
    student_id: studentId,
    student_code: code,
    student_name: name,
    knowledge_key: `knowledge_point:${label}`,
    knowledge_label: label,
    weighted_score_rate: 62,
    deduction_count: 1,
    item_count: 1,
    sample_reasons: '',
    source_question_refs: [],
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
  }
}

describe('knowledge graph view model', () => {
  it('uses the existing 90, 75 and 60 percent boundaries', () => {
    expect([0.9, 0.8999, 0.75, 0.7499, 0.6, 0.5999].map(masteryBand)).toEqual([
      'stable',
      'slight',
      'slight',
      'review',
      'review',
      'weak',
    ])
  })

  it('sorts nodes deterministically from weak to stable without links', () => {
    const model = buildEvidenceLaneModel([
      node('稳定', 0.93, 16),
      node('需要讲评', 0.62, 4),
      node('重点薄弱', 0.54, 1),
      node('轻微欠缺', 0.81, 9),
    ])
    expect(model.map((item) => item.knowledgeKey)).toEqual([
      'knowledge_point:重点薄弱',
      'knowledge_point:需要讲评',
      'knowledge_point:轻微欠缺',
      'knowledge_point:稳定',
    ])
    expect(model.map((item) => item.bandIndex)).toEqual([0, 1, 2, 3])
    expect(model.map((item) => item.percentLabel)).toEqual(['54%', '62%', '81%', '93%'])
  })

  it('uses a bounded monotonic node size for evidence count', () => {
    const sizes = [0, 1, 4, 100, 10_000].map(nodeEvidenceSize)
    expect(sizes[0]).toBeGreaterThanOrEqual(28)
    expect(sizes[sizes.length - 1]).toBeLessThanOrEqual(72)
    expect(sizes).toEqual([...sizes].sort((left, right) => left - right))
  })

  it('builds only scope, student and exact-tag grouping levels', () => {
    const repeated = row(12, 'S012', '匿名学生甲', '三角形全等')
    const tree = buildGroupingTree([
      row(15, 'S015', '匿名学生乙', '一次函数'),
      repeated,
      { ...repeated },
    ], '当前考试 · 七年级一班')

    expect(tree.name).toBe('当前考试 · 七年级一班')
    expect(tree.children.map((student) => student.name)).toEqual([
      'S012 匿名学生甲',
      'S015 匿名学生乙',
    ])
    expect(tree.children[0]?.children).toEqual([{
      name: '三角形全等',
      knowledgeKey: 'knowledge_point:三角形全等',
      children: [],
    }])
    expect(JSON.stringify(tree)).not.toMatch(/(?:parent|prerequisite|related)/)
  })

  it('summarizes 1000 nodes without non-finite layout values', () => {
    const nodes = Array.from({ length: 1000 }, (_, index) => (
      node(`匿名标签-${String(index + 1).padStart(4, '0')}`, (index % 101) / 100, index + 1)
    ))
    const model = buildEvidenceLaneModel(nodes)
    const summary = summarizeGraph(nodes)

    expect(model).toHaveLength(1000)
    expect(model.every((item) => Number.isFinite(item.size) && Number.isFinite(item.orderInBand)))
      .toBe(true)
    expect(summary.total).toBe(1000)
    expect(summary.stable + summary.slight + summary.review + summary.weak).toBe(1000)
  })
})
