const SESSION_STATUS_LABELS: Record<string, string> = {
  created: '待开始',
  pending: '待开始',
  grading: '批改中',
  completed: '已完成',
  failed: '有失败记录',
}

export type SessionStatusTone = 'done' | 'run' | 'danger'

/* 未知状态回退为原始值（沿用最近考试列表的旧行为） */
export function sessionStatusLabel(status: string): string {
  return SESSION_STATUS_LABELS[status] ?? status
}

/* 侧栏切换卡的状态文字色；未知状态返回 null（调用方不渲染状态） */
export function sessionStatusTone(status: string): SessionStatusTone | null {
  if (status === 'completed') return 'done'
  if (status === 'created' || status === 'pending' || status === 'grading') return 'run'
  if (status === 'failed') return 'danger'
  return null
}
