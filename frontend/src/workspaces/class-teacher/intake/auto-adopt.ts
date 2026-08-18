import { ApiError } from '@/api/errors'

import { intakeApi, type IntakeConversation } from '../api/intake'

export type AutoAdoptOutcome = 'none' | 'adopted' | 'conflict'

const attempted = new Set<string>()

/**
 * 轮次落定后把学生个人档案交接自动并入当前档案。
 * 409 冲突（学生资料已变化）不静默吞掉：返回 'conflict' 由页面保留手动入口。
 * 每个交接只自动尝试一次，避免轮询或重复落定造成重复写入。
 */
export async function autoAdoptStudentRecordHandoffs(next: IntakeConversation): Promise<AutoAdoptOutcome> {
  const candidates = next.handoffs.filter((item) =>
    ['pending', 'opened'].includes(item.adoption_state)
    && item.destination_key === 'class_teacher.student.record'
    && item.subject_ref_count === 1
    && !item.missing_fields.length
    && !attempted.has(item.handoff_id)
  )
  let outcome: AutoAdoptOutcome = 'none'
  for (const candidate of candidates) {
    attempted.add(candidate.handoff_id)
    try {
      const draft = await intakeApi.handoff(candidate.handoff_id)
      if (!['pending', 'opened'].includes(draft.adoption_state)) continue
      const target = draft.subject_refs[0]?.revision
      if (!target) continue
      await intakeApi.adopt(draft, target)
      outcome = 'adopted'
    } catch (value) {
      if (value instanceof ApiError && value.status === 409) outcome = 'conflict'
    }
  }
  return outcome
}
