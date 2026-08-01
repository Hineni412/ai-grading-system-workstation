import type { CurriculumVolume } from '../api/question-bank'

export function inferCurriculumVolumeId(
  name: string,
  volumes: readonly CurriculumVolume[],
): string {
  const normalized = name.replace(/\s+/g, '')
  const grade = normalized.match(/([七八九])年级/)?.[1] ?? ''
  const semesters = [
    ...(normalized.includes('上册') || normalized.includes('上学期') ? ['上'] : []),
    ...(normalized.includes('下册') || normalized.includes('下学期') ? ['下'] : []),
  ]
  if (!grade || semesters.length !== 1) return ''
  const gradeLabel = `${grade}年级`
  const semesterMark = semesters[0] ?? ''
  return volumes.find((item) =>
    item.grade === gradeLabel
    && (item.semester.includes(semesterMark) || item.label.includes(semesterMark)))?.id ?? ''
}
