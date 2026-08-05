import type { WorkspaceAITask } from './contracts'

export function dispatchEvidenceLabel(task: WorkspaceAITask): string {
  if (task.dispatch_evidence === 'response_persisted') return '响应已保存'
  if (task.dispatch_evidence === 'may_have_started') return '可能已发送'
  return '尚未发送'
}

export function handoffSummary(task: WorkspaceAITask): string {
  if (task.handoff_total === 0) return '暂无交接项'
  const handled = task.adopted_count + task.discarded_count + task.stale_count
  return `${handled}/${task.handoff_total} 已处理`
}

export function canCancelTask(task: WorkspaceAITask): boolean {
  return ['prepared', 'queued', 'running'].includes(task.status)
}

export function cancelTaskLabel(task: WorkspaceAITask): string {
  return task.dispatch_evidence === 'not_started' ? '取消' : '停止后续处理'
}

export function returnLocation(task: WorkspaceAITask): {
  path: string
  query: Record<string, string>
} {
  const path = task.module === 'teaching_prep' ? '/teaching-prep' : '/class-teacher'
  return {
    path,
    query: {
      destination: task.return_target,
      source_task_id: task.task_id,
      source_ref: task.source_ref.id,
    },
  }
}
