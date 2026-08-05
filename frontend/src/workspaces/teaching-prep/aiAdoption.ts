import type {
  WorkspaceAIHandoff,
  WorkspaceAITask,
} from '../shared/ai-tasks/contracts'

import {
  teachingPrepWorkbenchApi,
  type TeachingPrepAdoptionCommand,
  type TeachingPrepAIAdoption,
} from './api/workbench'

const ADOPTION_ROUTABLE_STATES = new Set([
  'pending',
  'opened',
  'adoption_started',
  'adopted',
])

export interface TeachingPrepAdoptionMatch {
  task: WorkspaceAITask
  handoff: WorkspaceAIHandoff
}

export function findTeachingPrepAdoption(
  tasks: WorkspaceAITask[],
  taskKind: string,
  proposalId: string,
): TeachingPrepAdoptionMatch | null {
  for (const task of tasks) {
    if (
      task.module !== 'teaching_prep'
      || task.task_kind !== taskKind
      || task.proposal_ref_id !== proposalId
    ) continue
    const handoff = task.handoffs.find(item => (
      item.module === 'teaching_prep'
      && item.draft_ref.id === proposalId
      && ADOPTION_ROUTABLE_STATES.has(item.adoption_state)
    ))
    if (handoff) return { task, handoff }
  }
  return null
}

export async function adoptTeachingPrepProposal(
  tasks: WorkspaceAITask[],
  taskKind: string,
  proposalId: string,
  command: TeachingPrepAdoptionCommand,
): Promise<{ match: TeachingPrepAdoptionMatch; receipt: TeachingPrepAIAdoption } | null> {
  const match = findTeachingPrepAdoption(tasks, taskKind, proposalId)
  if (!match) return null
  const receipt = await teachingPrepWorkbenchApi.adoptAIHandoff(
    match.handoff.handoff_id,
    match.handoff.draft_ref.revision,
    match.task.source_ref.revision,
    command,
  )
  return { match, receipt }
}
