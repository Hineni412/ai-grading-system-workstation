/* 教学学期短标签：「八年级上册」→「八上」，用于 56px 图标轨与密集列表的窄列 */
export function curriculumVolumeAbbrev(label: string | null | undefined): string {
  const value = label?.trim() ?? ''
  if (!value) return ''
  const match = /^(.)年级(.)册$/u.exec(value)
  if (match) return `${match[1]}${match[2]}`
  return value.slice(0, 2)
}
