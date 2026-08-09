import type { SemesterLessonProgressStatus } from './api/catalog'

export const LESSON_PROGRESS_OPTIONS: ReadonlyArray<{
  value: SemesterLessonProgressStatus
  label: string
}> = [
  { value: 'not_started', label: '未开始' },
  { value: 'preparing', label: '备课中' },
  { value: 'ready', label: '已备好' },
  { value: 'taught', label: '已授课' },
  { value: 'skipped', label: '本学期跳过' },
]

const LESSON_PROGRESS_LABELS = new Map(
  LESSON_PROGRESS_OPTIONS.map(option => [option.value, option.label]),
)

export function lessonProgressLabel(
  status: SemesterLessonProgressStatus | null | undefined,
): string {
  return status ? LESSON_PROGRESS_LABELS.get(status) ?? '未开始' : '未开始'
}
