import type {
  OverrideOperation,
  RecentNoteClassGroup,
  TimetableCell,
  TimetableSlot,
  TimetableWeek,
} from '../../../api/daily'

export type TimetableMode = 'view' | 'adjust' | 'regular'

export interface TimetableCellRef {
  day_of_week: number
  slot_key: string
}

export interface TimetableGridRow {
  slot: TimetableSlot
  /** 固定 5 格，下标 0..4 对应周一到周五。 */
  cells: TimetableCell[]
}

export const DAY_LABELS = ['周一', '周二', '周三', '周四', '周五'] as const

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

function parseIso(iso: string): { year: number; month: number; day: number } {
  const [year = 1970, month = 1, day = 1] = iso.split('-').map(Number)
  return { year, month, day }
}

function toIso(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

/** 以本地时区做日期加减，避免 UTC 解析造成的偏移。 */
export function isoAddDays(iso: string, days: number): string {
  const { year, month, day } = parseIso(iso)
  const date = new Date(year, month - 1, day)
  date.setDate(date.getDate() + days)
  return toIso(date)
}

/** 任意一天所在周的周一（ISO 日期）。 */
export function mondayOf(iso: string): string {
  const { year, month, day } = parseIso(iso)
  const date = new Date(year, month - 1, day)
  const weekday = date.getDay() === 0 ? 7 : date.getDay()
  date.setDate(date.getDate() - (weekday - 1))
  return toIso(date)
}

export function todayIso(): string {
  return toIso(new Date())
}

export function formatMonthDay(iso: string): string {
  const { month, day } = parseIso(iso)
  return `${month}月${day}日`
}

/** 周导航标题：第 N 周（9月1日–9月5日）；周次未设定时明确提示。 */
export function weekTitle(weekStart: string, weekNo: number | null): string {
  const range = `${formatMonthDay(weekStart)}–${formatMonthDay(isoAddDays(weekStart, 4))}`
  return weekNo === null ? `周次未设定（${range}）` : `第 ${weekNo} 周（${range}）`
}

export function dayLabel(dayOfWeek: number): string {
  return DAY_LABELS[dayOfWeek - 1] ?? `周${dayOfWeek}`
}

function emptyCell(dayOfWeek: number, slotKey: string): TimetableCell {
  return {
    day_of_week: dayOfWeek,
    slot_key: slotKey,
    course_text: '',
    class_label: '',
    source: 'empty',
    override_id: null,
    note: null,
  }
}

/** 把后端合并好的格子按「时段行 × 5 天列」整理成渲染模型。 */
export function buildGridRows(week: Pick<TimetableWeek, 'slots' | 'cells'>): TimetableGridRow[] {
  const byKey = new Map<string, TimetableCell>()
  for (const cell of week.cells) {
    byKey.set(`${cell.day_of_week}|${cell.slot_key}`, cell)
  }
  return week.slots.map((slot) => ({
    slot,
    cells: [1, 2, 3, 4, 5].map(
      (day) => byKey.get(`${day}|${slot.slot_key}`) ?? emptyCell(day, slot.slot_key),
    ),
  }))
}

function cellAt(cells: TimetableCell[], ref: TimetableCellRef): TimetableCell {
  return (
    cells.find(
      (cell) => cell.day_of_week === ref.day_of_week && cell.slot_key === ref.slot_key,
    ) ?? emptyCell(ref.day_of_week, ref.slot_key)
  )
}

/**
 * 拖拽生成当周临时调整（多 op 原子提交）：
 * 目标格为空 = 移动（目标 set + 来源 clear）；目标格有课 = 交换（两条 set）。
 */
export function buildDragOps(
  source: TimetableCellRef,
  target: TimetableCellRef,
  cells: TimetableCell[],
): OverrideOperation[] {
  if (source.day_of_week === target.day_of_week && source.slot_key === target.slot_key) return []
  const sourceCell = cellAt(cells, source)
  if (!sourceCell.course_text) return []
  const targetCell = cellAt(cells, target)
  const ops: OverrideOperation[] = [
    {
      day_of_week: target.day_of_week,
      slot_key: target.slot_key,
      action: 'set',
      course_text: sourceCell.course_text,
      class_label: sourceCell.class_label,
    },
  ]
  if (targetCell.course_text) {
    ops.push({
      day_of_week: source.day_of_week,
      slot_key: source.slot_key,
      action: 'set',
      course_text: targetCell.course_text,
      class_label: targetCell.class_label,
    })
  } else {
    ops.push({ day_of_week: source.day_of_week, slot_key: source.slot_key, action: 'clear' })
  }
  return ops
}

/** 时段 key → 显示名（已删除的自定义时段给出兜底文案）。 */
export function slotLabelMap(slots: TimetableSlot[]): Map<string, string> {
  return new Map(slots.map((slot) => [slot.slot_key, slot.label]))
}

/** 时段的时间文本：起止都有时显示「开始–结束」，只有一个时显示单个。 */
export function slotTime(slot: { start_text: string | null; end_text: string | null }): string {
  if (slot.start_text && slot.end_text) return `${slot.start_text}–${slot.end_text}`
  return slot.start_text ?? slot.end_text ?? ''
}

export function slotLabelFor(labels: Map<string, string>, slotKey: string): string {
  return labels.get(slotKey) ?? '已删除时段'
}

/** 今日提示条：当前查看周包含今天时，列出今天有内容的时段。 */
export function todaySummary(week: TimetableWeek): string[] {
  if (!week.today.in_week || week.today.day_of_week < 1 || week.today.day_of_week > 5) return []
  const labels = slotLabelMap(week.slots)
  return week.cells
    .filter((cell) => cell.day_of_week === week.today.day_of_week && cell.course_text)
    .map((cell) => {
      const course = cell.class_label
        ? `${cell.course_text}（${cell.class_label}）`
        : cell.course_text
      return `${slotLabelFor(labels, cell.slot_key)} ${course}`
    })
}

/** 课表中出现过的班级（去空、去重、保持首次出现顺序）。 */
export function distinctClassLabels(cells: TimetableCell[]): string[] {
  const seen = new Set<string>()
  const labels: string[] = []
  for (const cell of cells) {
    const label = cell.class_label.trim()
    if (label && !seen.has(label)) {
      seen.add(label)
      labels.push(label)
    }
  }
  return labels
}

/**
 * 进度对照默认班级：最近记录条数最多的前两个班；条数相同保持请求顺序。
 */
export function pickDefaultCompareClasses(
  groups: RecentNoteClassGroup[],
  count = 2,
): string[] {
  return groups
    .map((group, index) => ({ group, index }))
    .sort((a, b) => b.group.notes.length - a.group.notes.length || a.index - b.index)
    .slice(0, count)
    .map(({ group }) => group.class_label)
}

/** 自定义时段插入位置的显示文案：0 = 第 1 节前，1..8 = 第 N 节后。 */
export function customPositionText(position: number): string {
  return position <= 0 ? '第 1 节前' : `第 ${position} 节后`
}
