import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { questionJobFailures } from '../../api/question-bank'

export const QUESTION_BANK_LIBRARY_JOB_TYPES = new Set(['question_import', 'tagging_sync'])

export interface PaperQuestionRef {
  id: number
  paperId: number
  number: string
}

interface LeftoverGroup {
  key: string
  label: string
  questionIds: number[]
}

const FAILURE_GROUP_ORDER = [
  'timeout',
  'quality',
  'missing_image',
  'save',
  'criteria',
  'parse',
  'network',
  'rate_limit',
  'validation',
  'unknown',
  'review',
  'taxonomy',
] as const

const FAILURE_GROUP_LABEL: Record<(typeof FAILURE_GROUP_ORDER)[number], string> = {
  timeout: '分析超时',
  quality: '标签未达标',
  missing_image: '没有可用配图',
  save: '标签未能保存',
  criteria: '判定点未写出',
  parse: '分析结果无法识别',
  network: '暂时连不上分析服务',
  rate_limit: '请求过于频繁',
  validation: '暂时无法完成分析',
  unknown: '分析未完成',
  review: '判定点待您审核',
  taxonomy: '产生了待审核新词',
}

export function isQuestionBankLibraryJob(job: JobResponse): boolean {
  return QUESTION_BANK_LIBRARY_JOB_TYPES.has(job.job_type)
}

export function jobQuestionIds(job: JobResponse): number[] {
  const collected = [
    ...idList(job.payload.question_ids),
    ...idList(job.result.successful_question_ids),
    ...idList(job.result.failed_question_ids),
    ...idList(job.result.criteria_needs_review_question_ids),
    ...idList(job.result.review_question_ids),
  ]
  return [...new Set(collected)]
}

export function parseAnalysisProcessed(detail: string): { processed: number; total: number } | null {
  const match = detail.match(/AI 分析已处理 (\d+)\/(\d+) 道题/)
  if (!match) return null
  const processed = Number(match[1])
  const total = Number(match[2])
  if (!Number.isSafeInteger(processed) || !Number.isSafeInteger(total) || total <= 0) return null
  return { processed: Math.min(processed, total), total }
}

export function jobBelongsToPaper(
  job: JobResponse,
  paperId: number,
  refs: ReadonlyMap<number, PaperQuestionRef>,
  linkedPaperId?: number,
): boolean {
  if (linkedPaperId === paperId) return true
  return jobQuestionIds(job).some((questionId) => refs.get(questionId)?.paperId === paperId)
}

export function libraryJobDetailLine(job: JobResponse): string {
  if (job.job_type === 'question_import') {
    if (!TERMINAL_JOB_STATUSES.has(job.status)) return '试卷入库进行中，点这里回到试卷库'
    if (job.status === 'failed') return '试卷入库失败，点这里回到试卷库查看'
    if (job.status === 'cancelled') return '试卷入库已取消，点这里回到试卷库'
    return '试卷已入库，点这里回到试卷库'
  }
  if (!TERMINAL_JOB_STATUSES.has(job.status)) return '题库分析进行中，点这里回到试卷库'
  if (job.status === 'failed') return '题库分析失败，点这里回到试卷库查看'
  if (job.status === 'cancelled') return '题库分析已取消，点这里回到试卷库'
  if (paperLeftoverLines(job, new Map()).length > 0) {
    return '题库分析已结束，点这里回到试卷库查看未完成题目'
  }
  return '题库分析已结束，点这里回到试卷库'
}

export function paperLiveAnalysisLine(job: JobResponse): string {
  if (job.job_type === 'question_import') {
    return TERMINAL_JOB_STATUSES.has(job.status) ? '试卷已写入题库' : '正在把试卷写入题库…'
  }
  const processed = parseAnalysisProcessed(job.detail)
  if (processed) return `正在分析 ${processed.processed}/${processed.total} 道`
  if (!TERMINAL_JOB_STATUSES.has(job.status)) return '正在分析这张试卷…'
  return ''
}

export function formatQuestionLabel(numbers: string[]): string {
  const unique = [...new Set(numbers.map((value) => value.trim()).filter(Boolean))]
  unique.sort(compareQuestionNumber)
  if (unique.length === 0) return ''
  if (unique.length === 1) return `第${unique[0]}题`
  const last = unique[unique.length - 1]
  return `第${unique.slice(0, -1).join('、')}、${last}题`
}

export function paperLeftoverLines(
  job: JobResponse,
  refs: ReadonlyMap<number, PaperQuestionRef>,
  paperId?: number,
): string[] {
  if (job.job_type !== 'tagging_sync' || !TERMINAL_JOB_STATUSES.has(job.status)) return []
  return leftoverGroups(job)
    .map((group) => {
      const ids = paperId === undefined
        ? group.questionIds
        : group.questionIds.filter((questionId) => refs.get(questionId)?.paperId === paperId)
      if (ids.length === 0) return ''
      const numbers = ids
        .map((questionId) => refs.get(questionId)?.number)
        .filter((value): value is string => Boolean(value))
      if (numbers.length > 0) return `${formatQuestionLabel(numbers)}${group.label}`
      return `${ids.length} 道题${group.label}`
    })
    .filter(Boolean)
}

function leftoverGroups(job: JobResponse): LeftoverGroup[] {
  const assigned = new Set<number>()
  const buckets = new Map<string, number[]>()

  const push = (key: (typeof FAILURE_GROUP_ORDER)[number], questionId: number) => {
    if (questionId <= 0 || assigned.has(questionId)) return
    assigned.add(questionId)
    const ids = buckets.get(key) ?? []
    ids.push(questionId)
    buckets.set(key, ids)
  }

  for (const failure of questionJobFailures(job.result)) {
    push(groupKeyForCategory(failure.category), failure.question_id)
  }
  for (const questionId of idList(job.result.failed_question_ids)) {
    push('unknown', questionId)
  }
  for (const questionId of idList(job.result.criteria_needs_review_question_ids)) {
    push('review', questionId)
  }
  for (const questionId of idList(job.result.review_question_ids)) {
    push('taxonomy', questionId)
  }

  return FAILURE_GROUP_ORDER.flatMap((key) => {
    const questionIds = buckets.get(key)
    if (!questionIds?.length) return []
    return [{ key, label: FAILURE_GROUP_LABEL[key], questionIds }]
  })
}

function groupKeyForCategory(category: string): (typeof FAILURE_GROUP_ORDER)[number] {
  if (category === 'timeout') return 'timeout'
  if (category === 'quality') return 'quality'
  if (category === 'missing_image') return 'missing_image'
  if (category === 'save') return 'save'
  if (category === 'evidence' || category === 'training_criteria') return 'criteria'
  if (category === 'parse') return 'parse'
  if (category === 'network') return 'network'
  if (category === 'rate_limit') return 'rate_limit'
  if (category === 'validation') return 'validation'
  return 'unknown'
}

function idList(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  const ids: number[] = []
  for (const item of value) {
    if (Number.isSafeInteger(item) && Number(item) > 0) ids.push(Number(item))
  }
  return ids
}

function compareQuestionNumber(left: string, right: string): number {
  const leftMatch = left.match(/\d+/)
  const rightMatch = right.match(/\d+/)
  if (leftMatch && rightMatch) {
    const delta = Number(leftMatch[0]) - Number(rightMatch[0])
    if (delta !== 0) return delta
  }
  return left.localeCompare(right, 'zh-CN', { numeric: true })
}
