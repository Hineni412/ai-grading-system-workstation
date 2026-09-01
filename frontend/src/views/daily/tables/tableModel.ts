import type {
  DailyTableCellUpdate,
  DailyTableCells,
  DailyTableColumn,
  DailyTableColumnType,
  DailyTableSummary,
} from '../../../api/daily'

export const COLUMN_TYPE_LABELS: Record<DailyTableColumnType, string> = {
  text: '文本',
  number: '数字',
  check: '打勾',
  select: '单选',
  date: '日期',
}

/** 列类型下拉选项（新建向导与列管理共用）。 */
export const COLUMN_TYPE_OPTIONS = (
  Object.entries(COLUMN_TYPE_LABELS) as Array<[DailyTableColumnType, string]>
).map(([value, label]) => ({ value, label }))

export interface DailyTableGroups {
  active: DailyTableSummary[]
  archived: DailyTableSummary[]
}

/** 表格列表按状态分区：进行中在前、已归档在后，各自保持后端返回顺序。 */
export function partitionTables(tables: DailyTableSummary[]): DailyTableGroups {
  const groups: DailyTableGroups = { active: [], archived: [] }
  for (const table of tables) {
    groups[table.status === 'archived' ? 'archived' : 'active'].push(table)
  }
  return groups
}

export function cellKey(rowId: string, columnId: string): string {
  return `${rowId}|${columnId}`
}

export function savedCellValue(
  cells: DailyTableCells,
  rowId: string,
  columnId: string,
): string {
  return cells[rowId]?.[columnId] ?? ''
}

/**
 * 把编辑控件的输入转成批量 PUT 的单条 update：
 * check 归一成 '1'/'0'，其余类型取文本并去掉首尾空白；空串表示清除该格。
 */
export function buildCellUpdate(
  column: Pick<DailyTableColumn, 'id' | 'col_type'>,
  rowId: string,
  input: string | boolean,
): DailyTableCellUpdate {
  const valueText =
    column.col_type === 'check'
      ? input === true || input === '1'
        ? '1'
        : '0'
      : String(input).trim()
  return { row_id: rowId, column_id: column.id, value_text: valueText }
}

/** 保存前的轻量校验（后端仍做完整校验）；返回 null 表示可以提交。 */
export function cellInputError(
  column: Pick<DailyTableColumn, 'col_type'>,
  valueText: string,
): string | null {
  if (!valueText) return null
  if (valueText.length > 500) return '内容不能超过 500 字。'
  if (column.col_type === 'number' && !Number.isFinite(Number(valueText))) {
    return '数字列只能填写数字。'
  }
  return null
}

/** 解析单选列选项输入：逗号（中/英文）、顿号或换行分隔，去空去重。 */
export function parseOptionsInput(raw: string): string[] {
  const seen = new Set<string>()
  const options: string[] = []
  for (const part of raw.split(/[,，、\n]/)) {
    const option = part.trim()
    if (option && !seen.has(option)) {
      seen.add(option)
      options.push(option)
    }
  }
  return options
}

/** 列表里的最近更新时间：M月D日 HH:mm（本地时区）；解析失败时原样显示。 */
export function formatTableTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => (value < 10 ? `0${value}` : String(value))
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 从 Content-Disposition 取导出文件名（优先 filename*），取不到时用表名兜底。 */
export function csvFilenameFromDisposition(
  disposition: string | null,
  tableTitle: string,
): string {
  const fallback = `${tableTitle.trim() || '表格'}.csv`
  const encoded = disposition?.match(/filename\*\s*=\s*utf-8''([^;]+)/i)?.[1]
  let decoded = ''
  if (encoded) {
    try {
      decoded = decodeURIComponent(encoded.trim())
    } catch {
      decoded = ''
    }
  }
  const plain = disposition?.match(/filename\s*=\s*"?([^";]+)"?/i)?.[1]?.trim()
  const candidate = decoded || plain || fallback
  const basename = candidate.replace(/\\/g, '/').split('/').pop()?.trim()
  return basename || fallback
}
