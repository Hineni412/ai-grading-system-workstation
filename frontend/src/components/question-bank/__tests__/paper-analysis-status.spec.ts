import { describe, expect, it } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import {
  formatQuestionLabel,
  isQuestionBankLibraryJob,
  jobBelongsToPaper,
  libraryJobDetailLine,
  paperCurrentStatusLines,
  paperLeftoverLines,
  paperLiveAnalysisLine,
  parseAnalysisProcessed,
} from '../paper-analysis-status'

function job(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 255,
    job_type: 'tagging_sync',
    payload: { question_ids: [204, 207, 213, 217, 218] },
    result: {},
    status: 'succeeded',
    progress: 1,
    stage: 'tagging_sync',
    detail: 'partial',
    error: null,
    cancel_requested: false,
    created_at: '2026-08-17T01:55:00Z',
    started_at: '2026-08-17T01:55:01Z',
    updated_at: '2026-08-17T02:00:00Z',
    finished_at: '2026-08-17T02:00:00Z',
    ...overrides,
  }
}

const refs = new Map([
  [204, { id: 204, paperId: 12, number: '5' }],
  [207, { id: 207, paperId: 12, number: '8' }],
  [213, { id: 213, paperId: 12, number: '14' }],
  [217, { id: 217, paperId: 12, number: '18' }],
  [218, { id: 218, paperId: 12, number: '19' }],
])

describe('paper analysis status', () => {
  it('keeps import and tagging jobs as library jobs', () => {
    expect(isQuestionBankLibraryJob(job({ job_type: 'tagging_sync' }))).toBe(true)
    expect(isQuestionBankLibraryJob(job({ job_type: 'question_import' }))).toBe(true)
    expect(isQuestionBankLibraryJob(job({ job_type: 'grading_run' }))).toBe(false)
  })

  it('reads live analysis counts from the job detail', () => {
    expect(parseAnalysisProcessed('AI 分析已处理 5/20 道题')).toEqual({
      processed: 5,
      total: 20,
    })
    expect(paperLiveAnalysisLine(job({
      status: 'running',
      progress: 0.4,
      detail: 'AI 分析已处理 5/20 道题',
      finished_at: null,
    }))).toBe('正在分析 5/20 道')
  })

  it('merges evidence and criteria failures into one judgment-point leftover', () => {
    const lines = paperLeftoverLines(job({
      result: {
        outcome: 'partial',
        failed_question_ids: [204, 217, 218],
        criteria_needs_review_question_ids: [207, 213],
        failures: [
          { question_id: 204, category: 'quality', message: '分析结果未达到保存标准，需要补齐或审核。' },
          { question_id: 217, category: 'timeout', message: '分析超时，可稍后补齐未完成题目。' },
          { question_id: 218, category: 'timeout', message: '分析超时，可稍后补齐未完成题目。' },
          { question_id: 217, category: 'evidence', message: '解题证据未能保存。' },
          { question_id: 217, category: 'training_criteria', message: '训练判定点未能发布。' },
          { question_id: 218, category: 'evidence', message: '解题证据未能保存。' },
          { question_id: 218, category: 'training_criteria', message: '训练判定点未能发布。' },
        ],
      },
    }), refs, 12)

    expect(lines).toEqual([
      '第18、19题分析超时',
      '第5题标签未达标',
      '第8、14题判定点待您审核',
    ])
    expect(lines.join('')).not.toContain('证据')
  })

  it('does not treat a finished partial job as complete in the task center copy', () => {
    expect(libraryJobDetailLine(job({
      progress: 1,
      result: {
        failed_question_ids: [217],
        failures: [
          { question_id: 217, category: 'timeout', message: '分析超时，可稍后补齐未完成题目。' },
        ],
      },
    }))).toBe('题库分析已结束，点这里回到试卷库查看未完成题目')
    expect(libraryJobDetailLine(job({
      status: 'running',
      progress: 0.8,
      finished_at: null,
    }))).toBe('题库分析进行中，点这里回到试卷库')
  })

  it('matches a tagging job to the paper that owns its questions', () => {
    expect(jobBelongsToPaper(job(), 12, refs)).toBe(true)
    expect(jobBelongsToPaper(job(), 10, refs)).toBe(false)
    expect(jobBelongsToPaper(job(), 10, refs, 10)).toBe(true)
  })

  it('formats question numbers in reading order', () => {
    expect(formatQuestionLabel(['19', '5', '18'])).toBe('第5、18、19题')
  })

  it('uses current paper fields for leftover copy, not a finished job timeout', () => {
    expect(paperCurrentStatusLines({
      question_count: 20,
      tagged_question_count: 20,
      complete_analysis_count: 18,
      criteria_needs_review_count: 2,
    }, ['13', '16', '19'])).toEqual(['2 道题判定点待您审核'])
  })

  it('mentions missing tags instead of leftover analysis copy', () => {
    expect(paperCurrentStatusLines({
      question_count: 5,
      tagged_question_count: 0,
      complete_analysis_count: 0,
      criteria_needs_review_count: 0,
    }, ['1', '2', '3', '4', '5'])).toEqual(['5 道题标签未打全'])
  })

  it('lists incomplete question numbers only when tags are complete and nothing is waiting for review', () => {
    expect(paperCurrentStatusLines({
      question_count: 5,
      tagged_question_count: 5,
      complete_analysis_count: 3,
      criteria_needs_review_count: 0,
    }, ['1', '3'])).toEqual(['第1、3题分析未完成'])
  })
})
