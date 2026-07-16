import { afterEach, describe, expect, it, vi } from 'vitest'

import { decodeGraphRowsResponse } from '../graph'

const query = {
  scope: { mode: 'selected' as const, student_ids: ['12', '15'] },
  exam_scope: { mode: 'manual' as const, session_ids: [7, 8] },
}

const rows = {
  scope: { mode: 'selected', student_ids: ['12', '15'], class_id: null },
  exam_scope: {
    mode: 'manual',
    session_ids: [7, 8],
    sessions: [
      { session_id: 7, session_name: '七年级期中考试' },
      { session_id: 8, session_name: '七年级期末考试' },
    ],
  },
  rows: [],
  nodes: [{
    knowledge_key: 'knowledge_point:三角形全等',
    knowledge_label: '三角形全等',
    student_count: 2,
    item_count: 3,
    deduction_count: 1,
    average_mastery: 0.72,
    tag_context: { method: ['构造辅助线'] },
    error_counts: { primary: { 条件遗漏: 1 }, secondary: {} },
  }],
  edges: [],
  coverage: { covered_items: 3, total_items: 4, missing_items: { '8:Q2': 'missing_knowledge_point' } },
  warnings: ['知识图谱仅覆盖 3/4 个评分题；缺失题目已列出。'],
  diagnosis_identity: 'question_tag',
}

const evidence = {
  scope: rows.scope,
  exam_scope: rows.exam_scope,
  knowledge_key: 'knowledge_point:三角形全等',
  knowledge_label: '三角形全等',
  items: [{
    student_id: 12,
    student_code: 'S012',
    student_name: '匿名学生甲',
    class_id: '七年级一班',
    knowledge_key: 'knowledge_point:三角形全等',
    knowledge_label: '三角形全等',
    session_id: 7,
    session_name: '七年级期中考试',
    question_id: 'Q12(1)',
    bank_question_id: 101,
    score_awarded: 3,
    full_score: 5,
    score_rate: 0.6,
    tag_context: { method: ['构造辅助线'] },
    actionable_reasons: ['条件遗漏'],
    error_counts: { primary: { 条件遗漏: 1 }, secondary: {} },
  }],
  total: 1,
  page: 1,
  page_size: 20,
  total_pages: 1,
  coverage: rows.coverage,
  warnings: rows.warnings,
  diagnosis_identity: 'question_tag',
}

afterEach(() => vi.restoreAllMocks())

describe('P2-15 graph API contract', () => {
  it('rejects relationship edges even when their shape is valid', () => {
    expect(() => decodeGraphRowsResponse({
      ...rows,
      edges: [{
        source_key: 'knowledge_point:三角形全等',
        target_key: 'knowledge_point:全等三角形判定',
        relation_type: 'parent',
        weight: 1,
      }],
    })).toThrow('Invalid graph rows')
  })

  it('posts selected students and manually selected exams without relation controls', async () => {
    const graphModule = await import('../graph') as typeof import('../graph') & {
      fetchScopedGraphRows?: (input: typeof query) => Promise<unknown>
    }
    expect(typeof graphModule.fetchScopedGraphRows).toBe('function')

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(rows),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))
    await graphModule.fetchScopedGraphRows!(query)

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as Record<string, unknown>
    expect(body).toEqual(query)
    expect(JSON.stringify(body)).not.toMatch(/(?:relation|legacy|path)/i)
  })

  it('reuses the exact graph scope for paginated evidence', async () => {
    const graphModule = await import('../graph') as typeof import('../graph') & {
      fetchScopedGraphEvidence?: (
        input: typeof query,
        knowledgeKey: string,
        signal?: AbortSignal,
        page?: number,
      ) => Promise<unknown>
    }
    expect(typeof graphModule.fetchScopedGraphEvidence).toBe('function')

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(evidence),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))
    await graphModule.fetchScopedGraphEvidence!(query, evidence.knowledge_key, undefined, 1)

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      ...query,
      knowledge_key: evidence.knowledge_key,
      page: 1,
      page_size: 20,
    })
  })
})
