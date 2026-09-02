import { describe, expect, it } from 'vitest'

import type { PptxLocalOutput } from '../../api/workbench'
import { auditBadges, downloadFilename } from './outputAudit'

function output(overrides: Partial<PptxLocalOutput['audit']> = {}): PptxLocalOutput {
  return {
    id: 'o'.repeat(32),
    lesson_node_id: 'l'.repeat(32),
    version_number: 1,
    status: 'completed',
    output_filename: 'adapted.pptx',
    source_file_name: 'source.pptx',
    source_page_count: 24,
    final_page_count: 18,
    lesson_kind: 'review',
    audit: {
      superscript_subscript: { scanned_runs: 300, finding_count: 0, findings: [], passed: true },
      page_budget: { final_page_count: 18, limit: 18, over_limit: false, suggestion: '页数在课时容量红线内。' },
      question_pages: { checked: 2, pages: { 5: 101, 9: 102 }, problem_count: 0, problems: [], passed: true },
      passed: true,
      ...overrides,
    },
    inserted_question_pages: [{ question_id: 101, final_position: 5 }],
    worksheet_filename: null,
    created_at: '2026-08-03T00:00:00Z',
  }
}

describe('outputAudit view model', () => {
  it('maps a clean audit to three success badges', () => {
    const badges = auditBadges(output())
    expect(badges.map(badge => badge.tone)).toEqual(['success', 'success', 'success'])
    expect(badges.map(badge => badge.key)).toEqual(['superscript', 'pageBudget', 'questionPages'])
    expect(badges[2]?.detail).toContain('第 5、9 页')
  })

  it('flags suspicious superscript residue with the affected pages', () => {
    const badges = auditBadges(output({
      superscript_subscript: {
        scanned_runs: 300,
        finding_count: 2,
        findings: [
          { page: 4, baseline: 30000, kind: 'superscript', text: '整句被误标' },
          { page: 7, baseline: -25000, kind: 'subscript', text: '另一处' },
        ],
        passed: false,
      },
    }))
    expect(badges[0]?.tone).toBe('danger')
    expect(badges[0]?.label).toBe('上下标疑似错误 2 处')
    expect(badges[0]?.detail).toContain('第 4、7 页')
  })

  it('warns when the final page count crosses the lesson budget', () => {
    const badges = auditBadges(output({
      page_budget: { final_page_count: 21, limit: 18, over_limit: true, suggestion: '建议删页。' },
    }))
    expect(badges[1]?.tone).toBe('warning')
    expect(badges[1]?.label).toBe('页数超红线：21/18 页')
    expect(badges[1]?.detail).toBe('建议删页。')
  })

  it('flags question page conflicts as danger with the problem list', () => {
    const badges = auditBadges(output({
      question_pages: {
        checked: 2,
        pages: {},
        problem_count: 1,
        problems: ['题 101 与题 102 落在同一页 5，插入位置冲突。'],
        passed: false,
      },
    }))
    expect(badges[2]?.tone).toBe('danger')
    expect(badges[2]?.detail).toContain('同一页 5')
  })

  it('parses the RFC 5987 filename from the download response', () => {
    expect(downloadFilename(
      "attachment; filename*=UTF-8''%E5%AD%A6%E6%A1%88.docx",
      'fallback.docx',
    )).toBe('学案.docx')
    expect(downloadFilename('attachment; filename="adapted.pptx"', 'fallback.pptx'))
      .toBe('adapted.pptx')
    expect(downloadFilename(null, 'fallback.pptx')).toBe('fallback.pptx')
    expect(downloadFilename("attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87", 'fallback.pptx'))
      .toBe('中文')
  })
})
