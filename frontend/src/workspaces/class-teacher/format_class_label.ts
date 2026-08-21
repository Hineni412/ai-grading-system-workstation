/** 班主任工作台的班级名显示：名册里可能只存了数字（如 "9"），显示时补上"班"字。 */
export function formatClassLabel(label: string | null | undefined): string {
  const text = String(label ?? '').trim()
  if (!text) return '未分班'
  return /^\d+$/.test(text) ? `${text} 班` : text
}
