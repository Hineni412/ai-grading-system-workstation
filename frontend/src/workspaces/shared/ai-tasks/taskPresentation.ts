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
  if (task.module === 'teaching_prep') {
    const semesterRef = task.context_refs.find(ref => ref.kind === 'semester')
      ?? task.handoffs.flatMap(handoff => handoff.subject_refs).find(ref => ref.kind === 'semester')
    const destination = task.return_target
    if (destination === 'teaching_prep.library') {
      return {
        path,
        query: {
          view: 'library',
          ...(task.source_ref.kind === 'semester'
            ? { semester: task.source_ref.id }
            : semesterRef ? { semester: semesterRef.id } : {}),
          source_task_id: task.task_id,
        },
      }
    }
    if (destination === 'teaching_prep.overview') {
      return { path, query: { view: 'overview', source_task_id: task.task_id } }
    }
    const lessonRoutes: Partial<Record<string, readonly [string, string]>> = {
      'teaching_prep.lesson.materials': ['materials', 'sources'],
      'teaching_prep.lesson.plan': ['plan', 'plan'],
      'teaching_prep.lesson.exercises': ['materials', 'exercises'],
      'teaching_prep.lesson.slides': ['slides', 'slides'],
      'teaching_prep.lesson.package': ['package', 'package'],
    }
    const route = lessonRoutes[destination]
    if (route && task.source_ref.kind === 'lesson') {
      const focus = task.handoffs[0]?.return_focus_ref ?? task.proposal_ref_id
      return {
        path,
        query: {
          view: 'lesson',
          ...(semesterRef ? { semester: semesterRef.id } : {}),
          lesson: task.source_ref.id,
          stage: route[0], panel: route[1],
          ...(focus ? { focus_ref: focus } : {}),
          source_task_id: task.task_id,
        },
      }
    }
  }
  return {
    path,
    query: {
      destination: task.return_target,
      source_task_id: task.task_id,
      source_ref: task.source_ref.id,
    },
  }
}
