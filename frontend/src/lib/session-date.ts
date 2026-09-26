/* 考试列表与侧栏共用的短日期：「9 月 20 日」；无法解析时为空串 */
export function formatSessionDate(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
}
