import type { QuestionRepairPart, QuestionRepairPreview } from '../../api/question-bank'

export type RepairItem = QuestionRepairPreview['items'][number]
export type RepairStage = 'analysis' | 'matching' | 'organize' | 'review'
export const analysisParts: QuestionRepairPart[] = ['tags', 'evidence', 'criteria']
export const repairPartLabels: Record<QuestionRepairPart, string> = {
  tags: '基础标签与难度', evidence: '解题证据', criteria: '判定点',
  skills: '关键步骤关联', types: '题型', knowledge_points: '知识点关联',
}

// 共用清单展示口径；是否允许发起请求仍以后台 blocked_reason 为准。
export function repairStage(item: RepairItem): RepairStage {
  if (item.blocked_reason) {
    if (item.blocked_kind === 'standard' || item.blocked_kind === 'type_pending') return 'organize'
    // 兼容旧服务／缓存：只有阻断原因就是待归类原因时才归入整理。
    if (!item.blocked_kind && (item.blocked_reason === '当前知识标准不可用'
      || (!!item.type_pending_reason && item.blocked_reason === item.type_pending_reason))) return 'organize'
    return 'review'
  }
  return item.missing.some(part => analysisParts.includes(part)) ? 'analysis' : 'matching'
}

export function repairReason(item: RepairItem): string {
  if (item.blocked_reason) return item.blocked_reason
  if (repairStage(item) === 'analysis') return '基础分析尚未完整，先补齐缺少的分析资料，再检查标准匹配结果。'
  if (item.type_pending_reason) return `可补其他缺失关联；${item.type_pending_reason}。`
  if (item.missing.length === 1 && item.missing[0] === 'types') {
    return '已有基础分析，但尚无当前可用的题型归属，也没有可核验的无匹配结论。可补一次已有题型匹配。'
  }
  return '基础分析已经完成，仍缺少题型或判定点到标准的关联。缺少关联本身不代表词表没有对应条目。'
}

export function repairNext(item: RepairItem): string {
  switch (repairStage(item)) {
    case 'analysis': return '补基础分析，再检查缺失关联'
    case 'matching': return '补缺失匹配，保留已有分析'
    case 'organize': return item.type_pending_reason ? '按章整理题型后再归类' : '先核对或整理当前标准'
    case 'review': return item.blocked_kind === 'source' ? '先核对原题内容与图片' : '先核对题面、判定点或教师确认的关联'
  }
}
